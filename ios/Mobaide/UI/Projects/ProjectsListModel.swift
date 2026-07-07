import Foundation
import Observation
import SwiftUI

/// Projects list view state (Android `ProjectsViewModel`). The list is
/// bus-driven via `ProjectsRepository`; reorder keeps an optimistic ordering
/// until the `projects.changed` echo confirms it. Delete commits immediately
/// and Undo re-creates the project — the network delete has already happened,
/// so `linear_api_key` (never echoed by the server) cannot be restored.
@MainActor
@Observable
final class ProjectsListModel {
    private(set) var projects: [ProjectInfo] = []
    private(set) var isReconnecting = false
    var undoToast: UndoToast?
    var message: String?

    private let repository: ProjectsRepository
    private let agentEventBus: AgentEventBus
    private let sleeper: any Sleeper

    private var authoritative: [ProjectInfo] = []
    private var pendingOrder: [String]?
    private var lastDeleted: ProjectInfo?
    private var undoWindow: Task<Void, Never>?

    init(repository: ProjectsRepository, agentEventBus: AgentEventBus, sleeper: any Sleeper = ContinuousSleeper()) {
        self.repository = repository
        self.agentEventBus = agentEventBus
        self.sleeper = sleeper
        self.authoritative = repository.currentProjects
        self.projects = repository.currentProjects.sorted { $0.order < $1.order }
    }

    var isEmpty: Bool { projects.isEmpty && !isReconnecting }

    func observeProjects() async {
        for await list in repository.projects() {
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
        projects.move(fromOffsets: source, toOffset: destination)
        let names = projects.map(\.name)
        pendingOrder = names
        Task { [weak self] in await self?.commitReorder(names) }
    }

    private func commitReorder(_ names: [String]) async {
        do {
            _ = try await repository.reorderProjects(names)
        } catch {
            pendingOrder = nil
            recompute()
            message = "Reorder failed — reverted."
        }
    }

    private func recompute() {
        guard let pending = pendingOrder else {
            projects = authoritative.sorted { $0.order < $1.order }
            return
        }
        let repoOrder = authoritative.sorted { $0.order < $1.order }.map(\.name)
        if repoOrder == pending {
            pendingOrder = nil
            projects = authoritative.sorted { $0.order < $1.order }
        } else {
            projects = authoritative.sorted { index(of: $0.name, in: pending) < index(of: $1.name, in: pending) }
        }
    }

    private func index(of name: String, in order: [String]) -> Int {
        order.firstIndex(of: name) ?? Int.max
    }

    // MARK: - Delete + undo (immediate commit; undo re-creates)

    func delete(_ project: ProjectInfo) {
        Task { [weak self] in
            guard let self else { return }
            do {
                try await repository.deleteProject(name: project.name)
                showUndo(for: project)
            } catch let error as ProjectConflictError {
                message = error.detail
            } catch {
                message = "Delete failed: \(String(describing: error))"
            }
        }
    }

    private func showUndo(for project: ProjectInfo) {
        lastDeleted = project
        undoToast = UndoToast(message: "Deleted ‘\(project.name)’")
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
                _ = try await repository.createProject(snapshot.toUndoCreateBody())
                if snapshot.has_linear_key {
                    message = "Restored — re-enter the Linear API key in Edit."
                }
            } catch {
                message = "Undo failed: \(String(describing: error))"
            }
        }
    }
}

extension ProjectInfo {
    /// Compensating create body for undo. The server never echoes
    /// `linear_api_key`, so a restored project always starts keyless (Android
    /// `toUndoCreateBody`).
    func toUndoCreateBody() -> ProjectWrite {
        ProjectWrite(
            name: name,
            path: path,
            linear_api_key: nil,
            conda_env: conda_env,
            default_flags: default_flags,
            auto_scan_filter: auto_scan_filter
        )
    }
}
