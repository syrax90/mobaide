import Foundation
import Observation
import SwiftUI

/// Workspaces list view state (Android `WorkspacesViewModel`). The list is
/// bus-driven via `WorkspacesRepository`; reorder keeps an optimistic ordering
/// until the `projects.changed` echo confirms it. Delete commits immediately
/// and Undo re-creates the workspace — the network delete has already happened,
/// so `linear_api_key` (never echoed by the server) cannot be restored.
@MainActor
@Observable
final class WorkspacesListModel {
    private(set) var workspaces: [WorkspaceInfo] = []
    private(set) var isReconnecting = false
    var undoToast: UndoToast?
    var message: String?

    private let repository: WorkspacesRepository
    private let agentEventBus: AgentEventBus
    private let sleeper: any Sleeper

    private var authoritative: [WorkspaceInfo] = []
    private var pendingOrder: [String]?
    private var lastDeleted: WorkspaceInfo?
    private var undoWindow: Task<Void, Never>?

    init(repository: WorkspacesRepository, agentEventBus: AgentEventBus, sleeper: any Sleeper = ContinuousSleeper()) {
        self.repository = repository
        self.agentEventBus = agentEventBus
        self.sleeper = sleeper
        self.authoritative = repository.currentWorkspaces
        self.workspaces = repository.currentWorkspaces.sorted { $0.order < $1.order }
    }

    var isEmpty: Bool { workspaces.isEmpty && !isReconnecting }

    func observeWorkspaces() async {
        for await list in repository.workspaces() {
            authoritative = list
            recompute()
        }
    }

    func observeConnection() async {
        for await state in agentEventBus.connectionStates() {
            isReconnecting = state == .connecting
        }
    }

    func seed() async {
        await repository.seedIfEmpty()
    }

    func refresh() async {
        await agentEventBus.forceReconnect()
    }

    func clearMessage() { message = nil }

    // MARK: - Reorder (single PATCH of the full order; reconcile from the event)

    func move(from source: IndexSet, to destination: Int) {
        // In-place reorder drives the row-move animation (SwiftUI's
        // `move(fromOffsets:toOffset:)`, as in `SessionListModel`); the
        // authoritative cache is reconciled once `projects.changed` lands.
        workspaces.move(fromOffsets: source, toOffset: destination)
        let names = workspaces.map(\.name)
        pendingOrder = names
        Task { [weak self] in await self?.commitReorder(names) }
    }

    private func commitReorder(_ names: [String]) async {
        do {
            _ = try await repository.reorderWorkspaces(names)
        } catch {
            pendingOrder = nil
            recompute()
            message = "Reorder failed — reverted."
        }
    }

    private func recompute() {
        guard let pending = pendingOrder else {
            workspaces = authoritative.sorted { $0.order < $1.order }
            return
        }
        let repoOrder = authoritative.sorted { $0.order < $1.order }.map(\.name)
        if repoOrder == pending {
            pendingOrder = nil
            workspaces = authoritative.sorted { $0.order < $1.order }
        } else {
            workspaces = authoritative.sorted { index(of: $0.name, in: pending) < index(of: $1.name, in: pending) }
        }
    }

    private func index(of name: String, in order: [String]) -> Int {
        order.firstIndex(of: name) ?? Int.max
    }

    // MARK: - Delete + undo (immediate commit; undo re-creates)

    func delete(_ workspace: WorkspaceInfo) {
        Task { [weak self] in
            guard let self else { return }
            do {
                try await repository.deleteWorkspace(name: workspace.name)
                showUndo(for: workspace)
            } catch let error as WorkspaceConflictError {
                message = error.detail
            } catch {
                message = "Delete failed: \(String(describing: error))"
            }
        }
    }

    private func showUndo(for workspace: WorkspaceInfo) {
        lastDeleted = workspace
        undoToast = UndoToast(message: "Deleted ‘\(workspace.name)’")
        undoWindow?.cancel()
        undoWindow = Task { [weak self, sleeper] in
            try? await sleeper.sleep(for: .seconds(5))
            guard let self, !Task.isCancelled else { return }
            undoToast = nil
            lastDeleted = nil
        }
    }

    func undo() {
        undoWindow?.cancel()
        undoToast = nil
        guard let snapshot = lastDeleted else { return }
        lastDeleted = nil
        Task { [weak self] in
            guard let self else { return }
            do {
                _ = try await repository.createWorkspace(snapshot.toUndoCreateBody())
                if snapshot.has_linear_key {
                    message = "Restored — re-enter the Linear API key in Edit."
                }
            } catch {
                message = "Undo failed: \(String(describing: error))"
            }
        }
    }
}

extension WorkspaceInfo {
    /// Compensating create body for undo. The server never echoes
    /// `linear_api_key`, so a restored workspace always starts keyless (Android
    /// `toUndoCreateBody`).
    func toUndoCreateBody() -> WorkspaceWrite {
        WorkspaceWrite(
            name: name,
            path: path,
            linear_api_key: nil,
            conda_env: conda_env,
            default_flags: default_flags,
            auto_scan_filter: auto_scan_filter
        )
    }
}
