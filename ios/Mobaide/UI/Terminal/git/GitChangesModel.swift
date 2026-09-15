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

/// The confirmation Discard always goes through; never one tap.
struct DiscardPrompt: Equatable {
    let rows: [GitChangesRow]
    let title: String
    let body: String
}

enum CommitContextState: Equatable {
    case loading
    /// `author` nil: git cannot say who commits; `head` nil: nothing to amend.
    case ready(author: GitCommitAuthor?, head: GitCommitHead?)
    case failed(String)
}

struct CommitError: Equatable {
    let message: String
    let output: String?
}

struct CommitSheetState: Equatable {
    var context: CommitContextState = .loading
    var isCommitting = false
    var error: CommitError?
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
    /// Which mutating controls the agent advertises; none for an older agent.
    private(set) var capabilities = GitCapabilities.none
    /// Row ids ticked in edit mode, bound to the `List` selection.
    var selection: Set<String> = []
    /// A mutation is in flight: every control that would start another waits.
    private(set) var isMutating = false
    var discardPrompt: DiscardPrompt?
    var commitSheet: CommitSheetState?
    var toast: ToastMessage?
    /// Consumed by the screen's first `.task`: the terminal menu's Commit…
    /// lands here with the sheet already up.
    private(set) var openCommitOnAppear: Bool

    let sessionId: String
    private let agentAPI: AgentAPI
    private let agentEventBus: AgentEventBus
    private let projectName: @MainActor () -> String?
    private let sleeper: any Sleeper

    /// The etag cache. Lives as long as the screen is on the navigation stack,
    /// so a return from the Diff screen costs an `unchanged` round trip.
    private var cached: GitStatusResponse?
    private var loadGeneration = 0
    /// The rows a confirmation dialog acts on, kept apart from `discardPrompt`:
    /// SwiftUI writes the dialog's `isPresented` binding on dismissal, which
    /// can clear the prompt before the confirm action runs.
    private var pendingDiscard: [GitChangesRow] = []

    init(
        sessionId: String,
        agentAPI: AgentAPI,
        agentEventBus: AgentEventBus,
        projectName: @escaping @MainActor () -> String?,
        sleeper: any Sleeper = ContinuousSleeper(),
        openCommit: Bool = false
    ) {
        self.sessionId = sessionId
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        self.projectName = projectName
        self.sleeper = sleeper
        self.openCommitOnAppear = openCommit
    }

    var sections: [GitChangesSection] {
        if case .ready(_, _, let sections) = state { return sections }
        return []
    }

    var stagedCount: Int {
        if case .ready(let status, _, _) = state { return status.counts?.staged ?? 0 }
        return 0
    }

    /// Whether any shown row has a Stage / Unstage / Discard to offer.
    var hasActionableRows: Bool {
        sections.contains { section in
            section.rows.contains { GitChangesDisplay.rowActions($0.entry, capabilities: capabilities).any }
        }
    }

    var selectionSubsets: GitChangesDisplay.SelectionSubsets {
        GitChangesDisplay.selectionSubsets(sections, selection: selection, capabilities: capabilities)
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
            // Ticked rows that vanished with the refresh are dropped.
            let live = Set(sections.flatMap(\.rows).map(\.id))
            selection.formIntersection(live)
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

    /// One `GET /api/version` per screen visit, from the screen's own task so
    /// the poll's request sequence stays exactly the reads. An unreadable probe
    /// keeps the read-only viewer.
    func loadCapabilities() async {
        guard let info = try? await agentAPI.getAgentVersion() else { return }
        capabilities = info.gitCapabilities
    }

    /// The terminal menu's Commit…, once.
    func consumeOpenCommit() {
        guard openCommitOnAppear else { return }
        openCommitOnAppear = false
        openCommit()
    }

    func rows(for ids: Set<String>) -> [GitChangesRow] {
        sections.flatMap(\.rows).filter { ids.contains($0.id) }
    }

    func stage(_ rows: [GitChangesRow]) async {
        await mutate(rows) { [self] in
            try await agentAPI.stageSessionGit(sessionId: sessionId, paths: GitChangesDisplay.paths(for: rows))
        }
    }

    func unstage(_ rows: [GitChangesRow]) async {
        await mutate(rows) { [self] in
            try await agentAPI.unstageSessionGit(sessionId: sessionId, paths: GitChangesDisplay.paths(for: rows))
        }
    }

    func requestDiscard(_ rows: [GitChangesRow]) {
        guard !rows.isEmpty else { return }
        pendingDiscard = rows
        discardPrompt = GitChangesDisplay.discardPrompt(for: rows)
    }

    /// The dialog went away without a choice (Cancel, or a tap outside): the
    /// `isPresented` binding must not call this — SwiftUI writes it false
    /// before the confirm action runs, which would empty the rows underneath
    /// `confirmDiscard`. It clears `discardPrompt` alone.
    func dismissDiscard() {
        discardPrompt = nil
        pendingDiscard = []
    }

    /// One request per scope: a selection can mix modified and untracked
    /// files, and the route takes one scope at a time.
    func confirmDiscard() async {
        let rows = pendingDiscard
        pendingDiscard = []
        discardPrompt = nil
        await mutate(rows) { [self] in
            let byScope = Dictionary(grouping: rows) { GitChangesDisplay.discardScope(for: $0.entry) }
            for (scope, scoped) in byScope.sorted(by: { $0.key < $1.key }) {
                try await agentAPI.discardSessionGit(
                    sessionId: sessionId,
                    paths: GitChangesDisplay.paths(for: scoped),
                    scope: scope
                )
            }
        }
    }

    func openCommit() {
        commitSheet = CommitSheetState()
        Task { await loadCommitContext() }
    }

    func closeCommit() {
        guard commitSheet?.isCommitting != true else { return }
        commitSheet = nil
    }

    func loadCommitContext() async {
        guard commitSheet != nil else { return }
        do {
            let response = try await agentAPI.getSessionGitCommitContext(sessionId: sessionId)
            commitSheet?.context = .ready(author: response.author, head: response.head)
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            commitSheet?.context = .failed(SessionGitCopy.mutationMessage(for: SessionGitError.map(error)))
        }
    }

    func commit(summary: String, description: String, amend: Bool, noVerify: Bool) async {
        guard !isMutating, commitSheet != nil else { return }
        let message = GitChangesDisplay.composeCommitMessage(summary: summary, description: description)
        guard !message.isEmpty else { return }
        isMutating = true
        commitSheet?.isCommitting = true
        commitSheet?.error = nil
        defer { isMutating = false }
        do {
            let result = try await agentAPI.commitSessionGit(
                sessionId: sessionId,
                request: GitCommitRequest(message: message, amend: amend, no_verify: noVerify)
            )
            commitSheet = nil
            toast = ToastMessage(text: GitChangesDisplay.commitNotice(result), tone: .ok)
            await load()
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            let mapped = SessionGitError.map(error)
            var output: String?
            if case .git(let code, let detail)? = mapped as? SessionGitError, code == SessionGitError.codeHookFailed {
                output = detail
            }
            commitSheet?.isCommitting = false
            commitSheet?.error = CommitError(message: SessionGitCopy.mutationMessage(for: mapped), output: output)
        }
    }

    /// One mutation at a time; on success the ticked rows are forgotten and
    /// the list re-reads the working tree at once — the agent's `git.changed`
    /// echo would do the same a moment later through the etag path.
    private func mutate(_ rows: [GitChangesRow], _ body: () async throws -> Void) async {
        guard !rows.isEmpty, !isMutating else { return }
        isMutating = true
        defer { isMutating = false }
        do {
            try await body()
            selection.subtract(rows.map(\.id))
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            toast = ToastMessage(text: SessionGitCopy.mutationMessage(for: SessionGitError.map(error)), tone: .danger)
        }
        await load()
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

    /// Counts come from the full parse, but when the agent's byte cap hit
    /// before the entry cap they are a lower bound too — so the shown figure
    /// is what actually arrived and the total is never claimed exact.
    static func truncatedNotice(_ status: GitStatusResponse) -> String {
        let shown = status.entries.count
        if let total = status.counts?.total, total > shown {
            return "Showing the first \(shown) of at least \(total) entries."
        }
        return "List cut short after \(shown) entries."
    }

    /// What a row can do: a staged entry is unstaged, a modified or untracked
    /// one is staged or discarded, a conflict is resolved in the terminal
    /// (marking it resolved from a phone would stage the markers). Each gated
    /// on its capability, so an older agent shows no control that would 404.
    struct RowActions: Equatable {
        var stage = false
        var unstage = false
        var discard = false

        var any: Bool { stage || unstage || discard }
        static let none = RowActions()
    }

    static func rowActions(_ entry: GitStatusEntry, capabilities: GitCapabilities) -> RowActions {
        switch entry.group {
        case GitStatusEntry.groupStaged:
            return RowActions(unstage: capabilities.stage)
        case GitStatusEntry.groupUnstaged, GitStatusEntry.groupUntracked:
            return RowActions(stage: capabilities.stage, discard: capabilities.discard)
        default:
            return .none
        }
    }

    /// The discard route's scope for an entry; conflicts never reach it.
    static func discardScope(for entry: GitStatusEntry) -> String {
        entry.group == GitStatusEntry.groupUntracked
            ? GitDiscardRequest.scopeUntracked
            : GitDiscardRequest.scopeUnstaged
    }

    /// The paths a request names for these rows: a rename needs both its
    /// sides (`git reset` on the new name alone leaves the deletion staged).
    static func paths(for rows: [GitChangesRow]) -> [String] {
        var seen: Set<String> = []
        var out: [String] = []
        for row in rows {
            for path in [row.entry.path, row.entry.orig_path].compactMap({ $0 }) where seen.insert(path).inserted {
                out.append(path)
            }
        }
        return out
    }

    /// The selected rows each toolbar action applies to; a button is enabled
    /// when its list is non-empty.
    struct SelectionSubsets: Equatable {
        var stage: [GitChangesRow] = []
        var unstage: [GitChangesRow] = []
        var discard: [GitChangesRow] = []
    }

    static func selectionSubsets(
        _ sections: [GitChangesSection],
        selection: Set<String>,
        capabilities: GitCapabilities
    ) -> SelectionSubsets {
        var subsets = SelectionSubsets()
        for row in sections.flatMap(\.rows) where selection.contains(row.id) {
            let actions = rowActions(row.entry, capabilities: capabilities)
            if actions.stage { subsets.stage.append(row) }
            if actions.unstage { subsets.unstage.append(row) }
            if actions.discard { subsets.discard.append(row) }
        }
        return subsets
    }

    /// A section header's whole-section action; nil for Conflicts and unknown groups.
    struct SectionAction: Equatable {
        let label: String
        let stage: Bool
    }

    static func sectionAction(_ section: GitChangesSection, capabilities: GitCapabilities) -> SectionAction? {
        guard capabilities.stage else { return nil }
        switch section.group {
        case GitStatusEntry.groupStaged:
            return SectionAction(label: "Unstage all", stage: false)
        case GitStatusEntry.groupUnstaged, GitStatusEntry.groupUntracked:
            return SectionAction(label: "Stage all", stage: true)
        default:
            return nil
        }
    }

    static func discardPrompt(for rows: [GitChangesRow]) -> DiscardPrompt {
        if rows.count == 1, let only = rows.first {
            let untracked = only.entry.group == GitStatusEntry.groupUntracked
            return DiscardPrompt(
                rows: rows,
                title: untracked ? "Delete untracked file?" : "Discard changes?",
                body: untracked
                    ? "\(only.entry.path) is not tracked by git and will be deleted. This cannot be undone."
                    : "Changes to \(only.entry.path) will be thrown away. This cannot be undone."
            )
        }
        return DiscardPrompt(
            rows: rows,
            title: "Discard \(rows.count) files?",
            body: "Their working-tree changes will be thrown away and untracked ones deleted. This cannot be undone."
        )
    }

    /// `subject\n\nbody` — the shape `git commit -F` reads.
    static func composeCommitMessage(summary: String, description: String) -> String {
        let subject = summary.trimmingCharacters(in: .whitespacesAndNewlines)
        let body = description.trimmingCharacters(in: .whitespacesAndNewlines)
        return body.isEmpty ? subject : "\(subject)\n\n\(body)"
    }

    static func commitButtonLabel(stagedCount: Int, amend: Bool) -> String {
        if amend { return "Amend commit" }
        return stagedCount == 1 ? "Commit 1 file" : "Commit \(stagedCount) files"
    }

    static func commitNotice(_ result: GitCommitResponse) -> String {
        "Committed \(result.short_oid) · \(result.subject)"
    }

    static func authorLine(_ author: GitCommitAuthor) -> String {
        "Committing as \(author.name) <\(author.email)>"
    }
}
