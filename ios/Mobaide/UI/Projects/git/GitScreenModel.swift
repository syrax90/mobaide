import Foundation
import Observation

/// Git branches/worktrees screen state (Android `GitViewModel`). Items are
/// fetched on open and re-fetched whenever a `git.changed` for this project
/// lands. Delete is deferred: the request is sent only after a 5-second Undo
/// window elapses (`sleeper`); Undo cancels the pending Task so nothing reaches
/// the server. A `force_required` per-item result re-prompts and re-sends with
/// `force=true`; a 409 lists the busy holders.
@MainActor
@Observable
final class GitScreenModel {
    struct ForcePrompt {
        let items: [GitDeleteItem]
        let refs: [String]
    }

    private(set) var items: [GitItem] = []
    private(set) var isLoading = false
    var selected: Set<String> = []
    private(set) var inSelectionMode = false

    var undoToast: UndoToast?
    var forcePrompt: ForcePrompt?
    var busyDialog: [BusyHolder]?
    var message: String?

    let projectName: String
    private let agentAPI: AgentAPI
    private let agentEventBus: AgentEventBus
    private let sleeper: any Sleeper

    private var deferredCommit: Task<Void, Never>?
    private var loadGeneration = 0

    init(projectName: String, agentAPI: AgentAPI, agentEventBus: AgentEventBus, sleeper: any Sleeper = ContinuousSleeper()) {
        self.projectName = projectName
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        self.sleeper = sleeper
    }

    var isEmpty: Bool { items.isEmpty && !isLoading }

    func load() async {
        // Guard against overlapping loads (appear `.task`, pull-to-refresh, and
        // every `git.changed`): only the newest request applies its result, so a
        // slow earlier fetch can't overwrite fresher items.
        loadGeneration += 1
        let generation = loadGeneration
        isLoading = true
        defer { if generation == loadGeneration { isLoading = false } }
        do {
            let response = try await agentAPI.getGitItems(projectName: projectName)
            guard generation == loadGeneration else { return }
            items = response.items
            // Drop any selection for rows that no longer exist.
            let surviving = Set(items.map(\.id))
            selected = selected.intersection(surviving)
            if selected.isEmpty { inSelectionMode = false }
        } catch {
            guard generation == loadGeneration else { return }
            message = "Failed to load git items: \(String(describing: error))"
        }
    }

    /// Re-fetch when the agent reports this project's git tree changed.
    func observeGitChanged() async {
        for await event in agentEventBus.events() {
            if case .gitChanged(let name) = event, name == projectName {
                await load()
            }
        }
    }

    // MARK: - Selection

    func rowTapped(_ item: GitItem) {
        guard inSelectionMode else { return }
        toggle(item)
    }

    func rowLongPressed(_ item: GitItem) {
        guard item.isDeletable else { return }
        inSelectionMode = true
        selected.insert(item.id)
    }

    func toggle(_ item: GitItem) {
        guard item.isDeletable else { return }
        if selected.contains(item.id) {
            selected.remove(item.id)
        } else {
            selected.insert(item.id)
        }
        inSelectionMode = !selected.isEmpty
    }

    func exitSelection() {
        selected = []
        inSelectionMode = false
    }

    // MARK: - Delete (deferred commit; Undo cancels)

    func requestDelete() {
        let targets = items.filter { selected.contains($0.id) && $0.isDeletable }
        guard !targets.isEmpty else { return }
        let request = targets.map { GitDeleteItem(kind: $0.kind, branch: $0.branch, worktree_path: $0.worktree_path, force: false) }
        exitSelection()

        undoToast = UndoToast(message: "Deleting \(request.count) item(s)…")
        deferredCommit?.cancel()
        deferredCommit = Task { [weak self, sleeper] in
            do {
                try await sleeper.sleep(for: .seconds(5))
            } catch {
                return // Undo cancelled the window — nothing is sent.
            }
            guard let self, !Task.isCancelled else { return }
            undoToast = nil
            await sendDelete(request)
        }
    }

    func undo() {
        deferredCommit?.cancel()
        deferredCommit = nil
        undoToast = nil
    }

    func confirmForceDelete() {
        guard let prompt = forcePrompt else { return }
        forcePrompt = nil
        let forced = prompt.items.map { GitDeleteItem(kind: $0.kind, branch: $0.branch, worktree_path: $0.worktree_path, force: true) }
        Task { [weak self] in await self?.sendDelete(forced) }
    }

    func dismissForcePrompt() { forcePrompt = nil }
    func dismissBusy() { busyDialog = nil }

    private func sendDelete(_ request: [GitDeleteItem]) async {
        guard !request.isEmpty else { return }
        do {
            let response = try await agentAPI.deleteGitItems(projectName: projectName, request: GitDeleteRequest(items: request))

            var unmerged: [GitDeleteItem] = []
            for (index, result) in response.results.enumerated() where result.error == "force_required" {
                if index < request.count { unmerged.append(request[index]) }
            }
            if !unmerged.isEmpty {
                forcePrompt = ForcePrompt(items: unmerged, refs: unmerged.compactMap { $0.branch ?? $0.worktree_path })
            }

            let failed = response.results.filter { !$0.ok && $0.error != "force_required" }.count
            if failed > 0 {
                message = "\(failed) item(s) failed to delete"
            }
            // A successful delete makes the agent emit `git.changed`, which
            // `observeGitChanged` turns into a re-fetch.
        } catch let error as APIError where error.code == 409 {
            if let holders = ConflictDetail.busyHolders(from: error.errorBody) {
                busyDialog = holders
            } else {
                message = "Some items are in use."
            }
        } catch {
            message = "Delete failed: \(String(describing: error))"
        }
    }
}

/// Row display derivations shared by the git list (Android `GitItemRow`).
enum GitItemDisplay {
    static func primaryLabel(_ item: GitItem) -> String {
        switch item.kind {
        case GitItem.kindOrphanWorktree:
            return item.worktree_path ?? ""
        default:
            return item.branch ?? ""
        }
    }

    static func secondaryLabel(_ item: GitItem) -> String? {
        switch item.kind {
        case GitItem.kindBranchWithWorktree:
            return item.worktree_path
        case GitItem.kindOrphanWorktree:
            return item.detached ? "(detached HEAD)" : nil
        default:
            return nil
        }
    }

    enum ChipStyle {
        case highlight
        case danger
        case neutral
        case muted
    }

    static func chips(_ item: GitItem) -> [(text: String, style: ChipStyle)] {
        var chips: [(String, ChipStyle)] = []
        if item.is_current { chips.append(("current", .highlight)) }
        if item.gone { chips.append(("gone", .danger)) }
        if item.ahead > 0 || item.behind > 0 { chips.append(("ahead \(item.ahead) / behind \(item.behind)", .neutral)) }
        if item.locked {
            let reason = item.locked_reason?.trimmingCharacters(in: .whitespaces)
            chips.append((reason.map { $0.isEmpty ? "locked" : "locked: \($0)" } ?? "locked", .danger))
        }
        if item.kind == GitItem.kindOrphanWorktree { chips.append(("orphan", .danger)) }
        if item.prunable { chips.append(("prunable", .muted)) }
        return chips
    }
}
