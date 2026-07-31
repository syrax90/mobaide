import Foundation
import Observation

/// Git branches/worktrees screen state (Android `GitViewModel`). Items are
/// fetched on open and re-fetched whenever a `git.changed` for this project
/// lands. Delete is deferred: queued rows hide immediately and the request is
/// sent only after a 5-second Undo window elapses (`sleeper`); Undo cancels the
/// pending Task so nothing reaches the server. A delete raised while a window is
/// open joins the pending batch instead of replacing it. A `force_required`
/// per-item result re-prompts and re-sends with `force=true`; a 409 lists the
/// busy holders and restores every row, because the server rejects the batch
/// whole.
@MainActor
@Observable
final class GitScreenModel {
    /// A queued delete: the wire item plus the `GitItem.id` it came from, so the
    /// positional `results` array can be mapped back onto rows.
    struct PendingDelete: Equatable {
        let id: String
        let item: GitDeleteItem
    }

    struct ForcePrompt {
        let items: [PendingDelete]
        let refs: [String]
    }

    private(set) var items: [GitItem] = []
    private(set) var isLoading = false
    var selected: Set<String> = []

    var undoToast: UndoToast?
    var forcePrompt: ForcePrompt?
    var busyDialog: [BusyHolder]?
    var message: String?

    let projectName: String
    private let agentAPI: AgentAPI
    private let agentEventBus: AgentEventBus
    private let sleeper: any Sleeper

    /// Rows queued for deletion, hidden from the list so a swipe disappears at
    /// once instead of lingering through the Undo window. Every failure path
    /// restores them, and `load()` drops ids the server no longer lists.
    private var hiddenIDs: Set<String> = []
    /// The not-yet-sent batch, in request order. Held here rather than captured
    /// by `deferredCommit` so restarting the window to coalesce a second delete
    /// cannot discard the first one.
    private var pendingBatch: [PendingDelete] = []
    /// The force-delete re-send payload. Kept separately from `forcePrompt`
    /// because SwiftUI writes the dialog's `isPresented` binding on dismissal,
    /// which can clear `forcePrompt` before the confirm action runs.
    private var pendingForce: [PendingDelete] = []
    private var deferredCommit: Task<Void, Never>?
    private var loadGeneration = 0

    init(projectName: String, agentAPI: AgentAPI, agentEventBus: AgentEventBus, sleeper: any Sleeper = ContinuousSleeper()) {
        self.projectName = projectName
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        self.sleeper = sleeper
    }

    var visibleItems: [GitItem] { items.filter { !hiddenIDs.contains($0.id) } }

    var hasDeletableItems: Bool { visibleItems.contains(where: \.isDeletable) }

    var isEmpty: Bool { visibleItems.isEmpty && !isLoading }

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
            let surviving = Set(items.map(\.id))
            // Rows the server already dropped stay dropped; anything still
            // listed and no longer queued comes back into view.
            hiddenIDs.formIntersection(surviving)
            pendingBatch.removeAll { !surviving.contains($0.id) }
            if pendingBatch.isEmpty {
                deferredCommit?.cancel()
                deferredCommit = nil
                undoToast = nil
            }
            selected.formIntersection(surviving.subtracting(hiddenIDs))
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

    func select(_ item: GitItem) {
        guard item.isDeletable, !hiddenIDs.contains(item.id) else { return }
        selected.insert(item.id)
    }

    func clearSelection() {
        selected = []
    }

    // MARK: - Delete (deferred commit; Undo cancels)

    /// Single-row delete from a swipe or the context menu.
    func delete(_ item: GitItem) {
        enqueue([item])
    }

    /// Batch delete of the current selection.
    func requestDelete() {
        let targets = visibleItems.filter { selected.contains($0.id) && $0.isDeletable }
        selected = []
        enqueue(targets)
    }

    private func enqueue(_ targets: [GitItem]) {
        let queued = targets.filter { $0.isDeletable && !hiddenIDs.contains($0.id) }
        guard !queued.isEmpty else { return }
        for target in queued {
            hiddenIDs.insert(target.id)
            pendingBatch.append(PendingDelete(
                id: target.id,
                item: GitDeleteItem(
                    kind: target.kind,
                    branch: target.branch,
                    worktree_path: target.worktree_path,
                    force: false
                )
            ))
        }
        selected.subtract(hiddenIDs)

        undoToast = UndoToast(message: "Deleting \(pendingBatch.count) item(s)…")
        deferredCommit?.cancel()
        deferredCommit = Task { [weak self, sleeper] in
            do {
                try await sleeper.sleep(for: .seconds(5))
            } catch {
                return // Undo, or a coalescing restart, cancelled the window.
            }
            guard let self, !Task.isCancelled else { return }
            await commitPendingBatch()
        }
    }

    private func commitPendingBatch() async {
        let batch = pendingBatch
        pendingBatch = []
        deferredCommit = nil
        undoToast = nil
        await sendDelete(batch)
    }

    func undo() {
        deferredCommit?.cancel()
        deferredCommit = nil
        undoToast = nil
        hiddenIDs.subtract(pendingBatch.map(\.id))
        pendingBatch = []
    }

    func confirmForceDelete() {
        let forced = pendingForce.map { PendingDelete(id: $0.id, item: $0.item.forced()) }
        pendingForce = []
        forcePrompt = nil
        guard !forced.isEmpty else { return }
        hiddenIDs.formUnion(forced.map(\.id))
        Task { [weak self] in await self?.sendDelete(forced) }
    }

    func dismissForcePrompt() {
        pendingForce = []
        forcePrompt = nil
    }

    func dismissBusy() { busyDialog = nil }

    private func sendDelete(_ batch: [PendingDelete]) async {
        guard !batch.isEmpty else { return }
        do {
            let response = try await agentAPI.deleteGitItems(
                projectName: projectName,
                request: GitDeleteRequest(items: batch.map(\.item))
            )

            var unmerged: [PendingDelete] = []
            var failures: [GitDeleteResultItem] = []
            var restore: Set<String> = []
            for (index, result) in response.results.enumerated() where index < batch.count {
                let pending = batch[index]
                if result.error == "force_required" {
                    // Nothing was deleted, so the row comes back while the
                    // prompt names it; confirming re-hides it.
                    unmerged.append(pending)
                    restore.insert(pending.id)
                } else if !result.ok {
                    failures.append(result)
                    restore.insert(pending.id)
                }
            }
            // A short `results` array leaves those items unreported — treat them
            // as un-deleted so their rows come back.
            if response.results.count < batch.count {
                restore.formUnion(batch[response.results.count...].map(\.id))
            }
            hiddenIDs.subtract(restore)

            if !unmerged.isEmpty {
                pendingForce = unmerged
                forcePrompt = ForcePrompt(
                    items: unmerged,
                    refs: unmerged.compactMap { $0.item.branch ?? $0.item.worktree_path }
                )
            }
            if !failures.isEmpty {
                message = GitItemDisplay.deleteFailureMessage(failures)
            }
            // A successful delete makes the agent emit `git.changed`, which
            // `observeGitChanged` turns into a re-fetch.
        } catch let error as APIError where error.code == 409 {
            // The busy guard is all-or-nothing: nothing was processed.
            hiddenIDs.subtract(batch.map(\.id))
            if let holders = ConflictDetail.busyHolders(from: error.errorBody) {
                busyDialog = holders
            } else {
                message = "Some items are in use."
            }
        } catch {
            hiddenIDs.subtract(batch.map(\.id))
            message = "Delete failed: \(String(describing: error))"
        }
    }
}

private extension GitDeleteItem {
    func forced() -> GitDeleteItem {
        GitDeleteItem(kind: kind, branch: branch, worktree_path: worktree_path, force: true)
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

    static func deleteFailureMessage(_ failures: [GitDeleteResultItem]) -> String {
        guard failures.count == 1, let failure = failures.first else {
            return "\(failures.count) item(s) failed to delete"
        }
        return "Couldn't delete \(failure.target): \(deleteFailureReason(failure))"
    }

    /// `error_detail` is raw git stderr — first line, capped, so the single-line
    /// toast can't turn into a wall of text.
    static func deleteFailureReason(_ failure: GitDeleteResultItem) -> String {
        let detail = failure.error_detail?
            .split(separator: "\n").first
            .map { $0.trimmingCharacters(in: .whitespaces) } ?? ""
        if !detail.isEmpty { return String(detail.prefix(120)) }
        switch failure.error {
        case "locked": return "the worktree is locked"
        case "not_found": return "it no longer exists"
        case "invalid": return "the request was invalid"
        default: return "unknown error"
        }
    }
}
