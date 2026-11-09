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
/// whole. A batch holding a remote-tracking row is confirmed first — it reaches
/// everyone using the remote — and *Remove worktree* rides the same Undo
/// window. New branch and Check out are pessimistic: a typed or confirmed
/// request whose outcome the list shows through the same `git.changed` reload.
@MainActor
@Observable
final class GitScreenModel {
    /// The New branch sheet: `isCreating` blocks dismissal, `error` is the inline refusal.
    struct NewBranchSheetState: Equatable {
        var isCreating = false
        var error: String?
    }

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
    /// A checkout the busy guard refused; the user may retry it with `force`.
    var busyCheckoutPrompt: BusyCheckoutPrompt?
    /// Kept apart from `busyCheckoutPrompt` for the same reason as `pendingCheckout`.
    private var pendingBusyCheckout: BusyCheckoutPrompt?
    var message: String?

    /// Set from the screen's own task, never from `load()`: the tests pin the
    /// request sequence by index.
    private(set) var capabilities = GitCapabilities.none
    var remoteExpanded = false
    /// Tree folders the user flipped away from their default open/closed state.
    private(set) var toggledFolders: Set<String> = []
    /// When non-nil, the screen confirms this batch, which holds remote rows.
    var remoteDeletePrompt: [GitItem]?
    var filter = ""
    var newBranch: NewBranchSheetState?
    var checkoutPrompt: GitItem?
    private(set) var isCheckingOut = false
    /// The row the confirmation is about. Kept apart from `checkoutPrompt`
    /// because SwiftUI writes the dialog's `isPresented` binding on dismissal,
    /// which can clear the prompt before the confirm action runs.
    private var pendingCheckout: GitItem?

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
    /// The confirmed remote batch, apart from `remoteDeletePrompt` for the same
    /// dismissal-binding reason as `pendingForce`.
    private var pendingRemoteDelete: [GitItem] = []
    private var deferredCommit: Task<Void, Never>?
    private var loadGeneration = 0

    init(projectName: String, agentAPI: AgentAPI, agentEventBus: AgentEventBus, sleeper: any Sleeper = ContinuousSleeper()) {
        self.projectName = projectName
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        self.sleeper = sleeper
    }

    var visibleItems: [GitItem] { items.filter { !hiddenIDs.contains($0.id) } }

    func canDelete(_ item: GitItem) -> Bool {
        item.isDeletable(deleteRemote: capabilities.deleteRemote)
    }

    var hasDeletableItems: Bool { visibleItems.contains(where: canDelete) }

    var isEmpty: Bool { visibleItems.isEmpty && !isLoading }

    var canManageBranches: Bool { capabilities.branches }

    var filterable: Bool { visibleItems.count >= GitItemDisplay.filterThreshold }

    /// Text left in the field after the list shrank below the threshold is
    /// invisible, so it never counts.
    var filterActive: Bool {
        filterable && !filter.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private var shownItems: [GitItem] {
        filterActive ? visibleItems.filter { GitItemDisplay.matchesFilter($0, filter) } : visibleItems
    }

    var localItems: [GitItem] { shownItems.filter { $0.kind != GitItem.kindRemoteBranch } }

    var remoteItems: [GitItem] { shownItems.filter { $0.kind == GitItem.kindRemoteBranch } }

    var localRows: [GitTreeRow] {
        GitTree.rows(localItems, scope: .local, toggled: toggledFolders, filterActive: filterActive)
    }

    var remoteRows: [GitTreeRow] {
        GitTree.rows(remoteItems, scope: .remote, toggled: toggledFolders, filterActive: filterActive)
    }

    /// An active filter opens the remote section: hidden matches would read as "not found".
    var remoteOpen: Bool { remoteExpanded || filterActive }

    /// One `GET /api/version` per screen visit. An unreadable probe keeps the
    /// delete-only screen older agents get.
    func loadCapabilities() async {
        guard let info = try? await agentAPI.getAgentVersion() else { return }
        capabilities = info.gitCapabilities
    }

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
            if case .gitChanged(let name, _, _) = event, name == projectName {
                await load()
            }
        }
    }

    // MARK: - Selection

    func select(_ item: GitItem) {
        guard canDelete(item), !hiddenIDs.contains(item.id) else { return }
        selected.insert(item.id)
    }

    func clearSelection() {
        selected = []
    }

    // MARK: - Delete (deferred commit; Undo cancels)

    /// Single-row delete from a swipe or the context menu.
    func delete(_ item: GitItem) {
        queue([item])
    }

    /// Batch delete of the current selection.
    /// Keeps the selection while the remote confirmation is open, so Cancel
    /// returns to it; every other path clears it.
    func requestDelete() {
        let targets = visibleItems.filter { selected.contains($0.id) && canDelete($0) }
        queue(targets)
        if remoteDeletePrompt == nil { selected = [] }
    }

    private func queue(_ targets: [GitItem]) {
        let deletable = targets.filter(canDelete)
        if deletable.contains(where: { $0.kind == GitItem.kindRemoteBranch }) {
            pendingRemoteDelete = deletable
            remoteDeletePrompt = deletable
            return
        }
        enqueue(deletable)
    }

    func confirmRemoteDelete() {
        let targets = pendingRemoteDelete.filter(canDelete)
        pendingRemoteDelete = []
        remoteDeletePrompt = nil
        selected = []
        enqueue(targets)
    }

    func dismissRemoteDeletePrompt() {
        pendingRemoteDelete = []
        remoteDeletePrompt = nil
    }

    /// *Remove worktree*: an `orphan_worktree` item removes only the worktree at
    /// its path, whatever row it came from, so the branch survives and the row
    /// comes back as a plain branch on the `git.changed` reload.
    func removeWorktree(_ item: GitItem) {
        guard item.canRemoveWorktree, let path = item.worktree_path else { return }
        enqueueEntries([PendingDelete(
            id: item.id,
            item: GitDeleteItem(kind: GitItem.kindOrphanWorktree, worktree_path: path)
        )])
    }

    /// Ignored while a filter holds every matching folder open.
    func toggleFolder(_ id: String) {
        guard !filterActive else { return }
        if toggledFolders.contains(id) {
            toggledFolders.remove(id)
        } else {
            toggledFolders.insert(id)
        }
    }

    private func enqueue(_ targets: [GitItem]) {
        enqueueEntries(targets.map {
            PendingDelete(
                id: $0.id,
                item: GitDeleteItem(kind: $0.kind, branch: $0.branch, worktree_path: $0.worktree_path, force: false)
            )
        })
    }

    private func enqueueEntries(_ entries: [PendingDelete]) {
        let queued = entries.filter { !hiddenIDs.contains($0.id) }
        guard !queued.isEmpty else { return }
        for entry in queued {
            hiddenIDs.insert(entry.id)
            pendingBatch.append(entry)
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

    // MARK: - Branches (create, check out)

    func toggleRemote() { remoteExpanded.toggle() }

    func openNewBranch() {
        guard capabilities.branches else { return }
        newBranch = NewBranchSheetState()
    }

    func closeNewBranch() {
        guard newBranch?.isCreating != true else { return }
        newBranch = nil
    }

    /// `startPoint` nil means the agent's default, `HEAD` of the project root.
    func createBranch(name: String, startPoint: String?, checkout: Bool) async {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var sheet = newBranch, !sheet.isCreating, !trimmed.isEmpty else { return }
        sheet.isCreating = true
        sheet.error = nil
        newBranch = sheet
        do {
            let result = try await agentAPI.createGitBranch(
                projectName: projectName,
                request: GitBranchCreateRequest(name: trimmed, start_point: startPoint, checkout: checkout)
            )
            newBranch = nil
            message = GitItemDisplay.branchCreatedMessage(result)
        } catch {
            if SessionGitError.isCancellation(error) { return }
            if let holders = Self.busyHolders(in: error) {
                newBranch = nil
                offerForcedCheckout(BusyCheckoutPrompt(
                    holders: holders,
                    branch: trimmed,
                    retry: .create(GitBranchCreateRequest(name: trimmed, start_point: startPoint, checkout: checkout))
                ))
                return
            }
            newBranch = NewBranchSheetState(
                error: GitItemDisplay.createBranchErrorCopy(SessionGitError.map(error), name: trimmed)
            )
        }
    }

    func requestCheckout(_ item: GitItem) {
        guard capabilities.branches, item.canCheckOut, !isCheckingOut else { return }
        pendingCheckout = item
        checkoutPrompt = item
    }

    func dismissCheckoutPrompt() {
        pendingCheckout = nil
        checkoutPrompt = nil
    }

    func confirmCheckout() {
        guard let item = pendingCheckout, let ref = item.branch else { return }
        pendingCheckout = nil
        checkoutPrompt = nil
        isCheckingOut = true
        Task { [weak self] in await self?.checkout(item, ref: ref) }
    }

    private func checkout(_ item: GitItem, ref: String) async {
        defer { isCheckingOut = false }
        do {
            let result = try await agentAPI.checkoutGitRef(
                projectName: projectName,
                request: GitCheckoutRequest(ref: ref)
            )
            message = GitItemDisplay.checkedOutMessage(result)
        } catch {
            if SessionGitError.isCancellation(error) { return }
            if let holders = Self.busyHolders(in: error) {
                offerForcedCheckout(BusyCheckoutPrompt(
                    holders: holders,
                    branch: GitItemDisplay.primaryLabel(item),
                    retry: .checkout(ref: ref)
                ))
                return
            }
            message = GitItemDisplay.checkoutErrorCopy(
                SessionGitError.map(error),
                branch: GitItemDisplay.primaryLabel(item)
            )
        }
    }

    private func offerForcedCheckout(_ prompt: BusyCheckoutPrompt) {
        pendingBusyCheckout = prompt
        busyCheckoutPrompt = prompt
    }

    func dismissBusyCheckoutPrompt() {
        pendingBusyCheckout = nil
        busyCheckoutPrompt = nil
    }

    /// Retries the refused checkout with `force` after the user confirmed it.
    func confirmBusyCheckout() {
        guard let prompt = pendingBusyCheckout, !isCheckingOut else { return }
        pendingBusyCheckout = nil
        busyCheckoutPrompt = nil
        isCheckingOut = true
        Task { [weak self] in await self?.forceCheckout(prompt) }
    }

    private func forceCheckout(_ prompt: BusyCheckoutPrompt) async {
        defer { isCheckingOut = false }
        do {
            switch prompt.retry {
            case .checkout(let ref):
                let result = try await agentAPI.checkoutGitRef(
                    projectName: projectName,
                    request: GitCheckoutRequest(ref: ref, force: true)
                )
                message = GitItemDisplay.checkedOutMessage(result)
            case .create(var request):
                request.force = true
                let result = try await agentAPI.createGitBranch(projectName: projectName, request: request)
                message = GitItemDisplay.branchCreatedMessage(result)
            }
        } catch {
            if SessionGitError.isCancellation(error) { return }
            if Self.busyHolders(in: error) != nil {
                message = GitItemDisplay.busyCheckoutUnsupported
                return
            }
            let mapped = SessionGitError.map(error)
            switch prompt.retry {
            case .checkout:
                message = GitItemDisplay.checkoutErrorCopy(mapped, branch: prompt.branch)
            case .create(let request):
                message = GitItemDisplay.createBranchErrorCopy(mapped, name: request.name)
            }
        }
    }

    /// The busy `409` keeps the screen's own dialog; every other coded detail
    /// maps like the session routes.
    private static func busyHolders(in error: any Error) -> [BusyHolder]? {
        guard let apiError = error as? APIError, apiError.code == 409 else { return nil }
        return ConflictDetail.busyHolders(from: apiError.errorBody)
    }

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
    /// Below this many rows a filter field would take more space than it saves.
    static let filterThreshold = 8

    /// The coded refusals of the branch routes (PROTOCOL.md), on top of the session vocabulary.
    static let codeExists = "exists"
    static let codeBadBranchName = "bad_branch_name"
    static let codeUnknownStartPoint = "unknown_start_point"
    static let codeDirty = "dirty"
    static let codeCheckedOutElsewhere = "checked_out_elsewhere"
    static let codeOperationInProgress = "operation_in_progress"
    static let codeRefNotFound = "ref_not_found"

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

    /// `YYYY-MM-DD` from the agent's `iso-strict` committer date — a prefix, not a
    /// parse, so the day stays the committer's own on all three clients.
    static func shortDate(_ iso: String?) -> String? {
        guard let iso, !iso.isEmpty else { return nil }
        return iso.count > 10 ? String(iso.prefix(10)) : iso
    }

    /// The tip commit's subject and day, as the agent reported them.
    static func tipLine(_ item: GitItem) -> String? {
        let subject = item.subject?.trimmingCharacters(in: .whitespaces) ?? ""
        let date = shortDate(item.committed_at)
        if subject.isEmpty { return date }
        guard let date else { return subject }
        return "\(subject) · \(date)"
    }

    /// A case-insensitive substring match on the branch and the worktree path.
    static func matchesFilter(_ item: GitItem, _ filter: String) -> Bool {
        let needle = filter.trimmingCharacters(in: .whitespaces).lowercased()
        if needle.isEmpty { return true }
        return primaryLabel(item).lowercased().contains(needle)
            || (item.worktree_path ?? "").lowercased().contains(needle)
    }

    static func chips(_ item: GitItem) -> [(text: String, style: ChipStyle)] {
        var chips: [(String, ChipStyle)] = []
        if item.is_current { chips.append(("current", .highlight)) }
        if item.kind == GitItem.kindRemoteBranch { chips.append((item.remote ?? "remote", .muted)) }
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
        // A remote delete's push failures share the fetch/pull/push vocabulary;
        // git's own wording for a disabled prompt says nothing about what to fix.
        if failure.error == GitJobResponse.codeAuthRequired {
            return GitChangesDisplay.jobErrorCopy(failure.error, message: nil)
        }
        let detail = failure.error_detail?
            .split(separator: "\n").first
            .map { $0.trimmingCharacters(in: .whitespaces) } ?? ""
        if !detail.isEmpty { return String(detail.prefix(120)) }
        switch failure.error {
        case "locked": return "the worktree is locked"
        case "not_found": return "it no longer exists"
        case "invalid": return "the request was invalid"
        case SessionGitError.codeGitTimeout: return GitChangesDisplay.jobErrorCopy(failure.error, message: nil)
        default: return "unknown error"
        }
    }
}

/// A checkout the busy guard refused, kept so the user can retry it with `force`.
struct BusyCheckoutPrompt: Equatable {
    enum Retry: Equatable {
        case checkout(ref: String)
        case create(GitBranchCreateRequest)
    }

    let holders: [BusyHolder]
    let branch: String
    let retry: Retry
}

extension GitItemDisplay {
    static func remoteDeleteMessage(_ refs: [String]) -> String {
        let what = refs.count == 1 ? refs[0] : "\(refs.count) branches"
        let pronoun = refs.count == 1 ? "it" : "them"
        return "Delete \(what) on the remote? This removes \(pronoun) for everyone with access to the remote."
    }

    static func checkoutConfirmMessage(branch: String, projectName: String) -> String {
        "Check out \(branch) in the project folder of \(projectName)? "
            + "Sessions started in that folder afterwards use this branch."
    }

    static let busyCheckoutTitle = "Project folder in use"

    /// Body of the soft busy prompt; the holders follow it. Same copy on Android and the web.
    static func busyCheckoutMessage(branch: String) -> String {
        "These items work in the project folder. Checking out \(branch) changes "
            + "the files under them. Check out anyway?"
    }

    /// A forced retry still refused: only an agent without `force` does that.
    static let busyCheckoutUnsupported =
        "The project folder is still in use. Update the agent to check out anyway."

    static func checkedOutMessage(_ result: GitBranchResponse) -> String {
        "Checked out \(result.branch)"
    }

    static func branchCreatedMessage(_ result: GitBranchResponse) -> String {
        result.checked_out ? "Created and checked out \(result.branch)" : "Created \(result.branch)"
    }

    /// The checkout refusals, word for word what Android and the web show.
    static func checkoutErrorCopy(_ error: any Error, branch: String) -> String {
        if case .git(let code, let message)? = error as? SessionGitError {
            switch code {
            case codeDirty:
                return "The project folder has local changes this checkout would overwrite. "
                    + "Commit or discard them from a session's Changes screen first."
            case codeCheckedOutElsewhere:
                if let message, !message.isEmpty {
                    return "\(branch) is already checked out in another worktree (\(message))."
                }
                return "\(branch) is already checked out in another worktree."
            case codeOperationInProgress:
                return "A merge, rebase or cherry-pick is in progress in the project folder. "
                    + "Finish or abort it first."
            case SessionGitError.codeJobRunning:
                return "A fetch, pull or push is still running in the project folder."
            case codeRefNotFound:
                return "Branch not found — refresh the list."
            default:
                break
            }
        }
        return SessionGitCopy.errorMessage(for: error)
    }

    /// The create refusals shown inline in the New branch sheet.
    static func createBranchErrorCopy(_ error: any Error, name: String) -> String {
        if case .git(let code, let message)? = error as? SessionGitError {
            switch code {
            case codeExists:
                return "A branch named \(name) already exists."
            case codeBadBranchName:
                return "Not a valid branch name."
            case codeUnknownStartPoint:
                if let message, !message.isEmpty {
                    return "Start point \(message) was not found."
                }
                return "The start point was not found."
            default:
                break
            }
        }
        return checkoutErrorCopy(error, branch: name)
    }
}
