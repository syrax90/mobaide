import Foundation
import Observation

/// One group of the Changes list (Conflicts / Staged / Changes / Untracked).
struct GitChangesSection: Equatable, Identifiable {
    let group: String
    let title: String
    let rows: [GitChangesRow]

    var id: String { group }
}

struct GitChangesRow: Equatable, Identifiable {
    /// `group:occurrence:path`, occurrence ahead of the path so no filename can
    /// forge another row's key (two names whose invalid bytes both decode to
    /// U+FFFD collide on the bare path).
    let id: String
    let entry: GitStatusEntry
}

/// Session-scoped Changes screen state (Android `GitChangesViewModel` +
/// `GitRepository.observeSessionGitStatus`).
///
/// Polling, not just push: `git.changed` only fires for the app's own git
/// mutations, so a commit typed into the PTY — the whole point of the viewer —
/// never announces itself. `run()` therefore polls while the screen is shown
/// and the app is not in the background; each request carries the last `etag`,
/// so an idle repo costs a tiny `unchanged` round trip. The feed ends after an
/// old-agent 404 or a session-not-found 404: neither answer can change, so
/// there is nothing left to poll — a later pull-to-refresh answers once but
/// does not re-arm the poll.
@MainActor
@Observable
final class GitChangesModel {
    enum State: Equatable {
        case loading
        case ready(status: GitStatusResponse, repo: GitRepoInfo, sections: [GitChangesSection])
        case unavailable(reason: String, cwd: String?)
        case agentOutdated
        case sessionGone
        case error(String)
    }

    static let pollInterval: Duration = .seconds(10)

    private(set) var state: State = .loading
    /// A failed refresh over content already shown; cleared by the next success.
    private(set) var staleError: String?
    private(set) var isLoading = false

    let sessionId: String
    private let agentAPI: AgentAPI
    private let agentEventBus: AgentEventBus
    private let projectName: @MainActor () -> String?
    private let sleeper: any Sleeper

    /// The etag cache. Lives as long as the screen is on the navigation stack,
    /// so a return from the Diff screen costs an `unchanged` round trip.
    private var cached: GitStatusResponse?
    private var loadGeneration = 0

    init(
        sessionId: String,
        agentAPI: AgentAPI,
        agentEventBus: AgentEventBus,
        projectName: @escaping @MainActor () -> String?,
        sleeper: any Sleeper = ContinuousSleeper()
    ) {
        self.sessionId = sessionId
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        self.projectName = projectName
        self.sleeper = sleeper
    }

    /// Neither 404 can change, so polling stops here.
    var isFinished: Bool {
        switch state {
        case .agentOutdated, .sessionGone: return true
        default: return false
        }
    }

    /// The visible-only poll: an immediate load, then one per interval until
    /// cancelled or the feed ends.
    func run() async {
        while !Task.isCancelled {
            await load()
            if isFinished { return }
            do {
                try await sleeper.sleep(for: Self.pollInterval)
            } catch {
                return
            }
        }
    }

    func load() async {
        // Only the newest request applies its result (appear, poll, push and
        // pull-to-refresh all call this), so a slow earlier fetch cannot
        // overwrite fresher content.
        loadGeneration += 1
        let generation = loadGeneration
        isLoading = true
        defer { if generation == loadGeneration { isLoading = false } }
        // A retry with nothing to show must visibly restart, or an identical
        // failure would look like the tap was ignored.
        if cached == nil, case .error = state {
            state = .loading
        }
        do {
            let fresh = try await agentAPI.getSessionGitStatus(sessionId: sessionId, etag: cached?.etag)
            guard generation == loadGeneration else { return }
            let body: GitStatusResponse
            if !fresh.unchanged {
                body = fresh
            } else if let cached {
                body = cached
            } else {
                body = try await agentAPI.getSessionGitStatus(sessionId: sessionId, etag: nil)
                guard generation == loadGeneration else { return }
            }
            cached = body
            staleError = nil
            state = Self.state(for: body)
        } catch {
            guard generation == loadGeneration, !SessionGitError.isCancellation(error) else { return }
            apply(SessionGitError.map(error))
        }
    }

    /// Re-fetch when the agent reports a git change for this session's
    /// repository — by project name, or by the resolved `toplevel` /
    /// `common_dir` for a session without one. Before the first body nothing
    /// is known, so every push refreshes.
    func observeGitChanged() async {
        for await event in agentEventBus.events() {
            guard case .gitChanged(let name, let toplevel, let commonDir) = event,
                  matches(projectName: name, toplevel: toplevel, commonDir: commonDir)
            else { continue }
            await load()
        }
    }

    func diffRoute(for entry: GitStatusEntry) -> Route {
        .sessionGitDiff(
            sessionId: sessionId,
            path: entry.path,
            scope: GitChangesDisplay.diffScope(for: entry),
            origPath: entry.orig_path
        )
    }

    private func matches(projectName eventProject: String, toplevel: String?, commonDir: String?) -> Bool {
        guard let repo = cached?.repo else { return true }
        if let own = projectName(), own == eventProject { return true }
        if let toplevel, toplevel == repo.toplevel { return true }
        return commonDir != nil && commonDir == repo.common_dir
    }

    private func apply(_ error: any Error) {
        switch error as? SessionGitError {
        case .agentOutdated?:
            state = .agentOutdated
        case .sessionGone?:
            state = .sessionGone
        default:
            let message = SessionGitCopy.errorMessage(for: error)
            if cached != nil {
                staleError = message
            } else {
                state = .error(message)
            }
        }
    }

    private static func state(for body: GitStatusResponse) -> State {
        guard let repo = body.repo, body.unavailable_reason == nil else {
            return .unavailable(
                reason: body.unavailable_reason ?? GitStatusResponse.unavailableNotARepo,
                cwd: body.cwd
            )
        }
        return .ready(status: body, repo: repo, sections: GitChangesDisplay.sections(for: body))
    }
}

/// Pure derivations for the Changes screen (Android `GitChangesUiState.kt`).
enum GitChangesDisplay {
    struct Badge: Equatable {
        let label: String
        let tone: PixelTone
    }

    static let sectionOrder: [(group: String, title: String)] = [
        (GitStatusEntry.groupConflicts, "Conflicts"),
        (GitStatusEntry.groupStaged, "Staged"),
        (GitStatusEntry.groupUnstaged, "Changes"),
        (GitStatusEntry.groupUntracked, "Untracked"),
    ]

    /// The known groups in their fixed order, then any group the agent adds
    /// later, sorted, so a new value renders instead of vanishing.
    static func sections(for status: GitStatusResponse) -> [GitChangesSection] {
        let byGroup = Dictionary(grouping: status.entries, by: \.group)
        let known = sectionOrder.compactMap { order in
            byGroup[order.group].map { section(order.group, title: order.title, entries: $0) }
        }
        let other = byGroup.keys
            .filter { group in !sectionOrder.contains { $0.group == group } }
            .sorted()
            .compactMap { group in
                byGroup[group].map { section(group, title: group.replacingOccurrences(of: "_", with: " "), entries: $0) }
            }
        return known + other
    }

    private static func section(_ group: String, title: String, entries: [GitStatusEntry]) -> GitChangesSection {
        var seen: [String: Int] = [:]
        let rows = entries.map { entry -> GitChangesRow in
            let occurrence = (seen[entry.path] ?? 0) + 1
            seen[entry.path] = occurrence
            return GitChangesRow(id: "\(group):\(occurrence):\(entry.path)", entry: entry)
        }
        return GitChangesSection(group: group, title: title, rows: rows)
    }

    /// Conflicted files are diffed against HEAD, which the agent serves as
    /// `unstaged`.
    static func diffScope(for entry: GitStatusEntry) -> String {
        switch entry.group {
        case GitStatusEntry.groupStaged: return GitDiffResponse.scopeStaged
        case GitStatusEntry.groupUntracked: return GitDiffResponse.scopeUntracked
        default: return GitDiffResponse.scopeUnstaged
        }
    }

    static func badge(for entry: GitStatusEntry) -> Badge {
        switch entry.group {
        case GitStatusEntry.groupConflicts:
            return Badge(label: entry.code.isEmpty ? "U" : entry.code, tone: .danger)
        case GitStatusEntry.groupUntracked:
            return Badge(label: "?", tone: .muted)
        default:
            switch entry.code {
            case "A": return Badge(label: "A", tone: .ok)
            case "D": return Badge(label: "D", tone: .danger)
            case "R", "C": return Badge(label: entry.code, tone: .accent)
            default: return Badge(label: entry.code.isEmpty ? "M" : entry.code, tone: .warning)
            }
        }
    }

    static func headLabel(_ repo: GitRepoInfo) -> String {
        let head = repo.head
        if head.initial { return "No commits yet" }
        if head.detached { return "detached @ \(head.short_oid ?? "HEAD")" }
        return head.branch ?? head.short_oid ?? "HEAD"
    }

    static func upstreamLabel(_ upstream: GitUpstream?) -> String? {
        guard let upstream else { return nil }
        guard let ahead = upstream.ahead, let behind = upstream.behind else {
            return "\(upstream.ref) · upstream gone"
        }
        return "↑\(ahead) ↓\(behind) · \(upstream.ref)"
    }

    static func stateLabel(_ state: String) -> String? {
        switch state {
        case GitRepoInfo.stateClean: return nil
        case GitRepoInfo.stateCherryPicking: return "cherry-picking"
        default: return state.replacingOccurrences(of: "_", with: " ")
        }
    }

    static func secondaryLabel(_ entry: GitStatusEntry) -> String? {
        if let orig = entry.orig_path { return "\(orig) → \(entry.path)" }
        return entry.directory
    }

    static func truncatedNotice(_ status: GitStatusResponse) -> String {
        let total = status.counts.map { String($0.total) } ?? "many"
        return "Showing the first \(status.entry_limit) of \(total) entries."
    }
}
