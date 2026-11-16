import Foundation

/// App-scoped live workspace list (Android `WorkspacesRepository`). Push-driven off
/// the agent event bus: every `projects.changed` carries the full authoritative
/// snapshot, so mutations never patch local state — they call REST and the bus
/// echo reconciles the cache. REST seeds the cache only while it is still empty.
@MainActor
final class WorkspacesRepository {
    private let agentAPI: AgentAPI
    private let agentEventBus: AgentEventBus
    private let broadcaster = Broadcaster<[WorkspaceInfo]>.stateFlow()

    private(set) var currentWorkspaces: [WorkspaceInfo] = []
    private var subscription: Task<Void, Never>?

    // In-session Linear metadata cache keyed by workspace name, feeding the form's
    // auto-scan filter subform so reopening a workspace does not refetch.
    private var labelCache: [String: [LinearLabelDto]] = [:]
    private var stateCache: [String: [LinearStateDto]] = [:]

    init(agentAPI: AgentAPI, agentEventBus: AgentEventBus) {
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        broadcaster.emit([])
    }

    func workspaces() -> AsyncStream<[WorkspaceInfo]> {
        broadcaster.subscribe()
    }

    /// Idempotent. Subscribe before `AgentEventBus.start()` so the snapshot the
    /// bus re-pushes on connect is not missed (the events stream has no replay).
    func start() {
        guard subscription == nil else { return }
        let stream = agentEventBus.events()
        subscription = Task { [weak self] in
            for await event in stream {
                await self?.apply(event)
            }
        }
    }

    private func apply(_ event: AgentEvent) {
        guard case .workspacesChanged(let list) = event else { return }
        setWorkspaces(list)
    }

    private func setWorkspaces(_ list: [WorkspaceInfo]) {
        currentWorkspaces = list
        broadcaster.emit(list)
    }

    /// One-shot REST seed, used only when the bus has not populated the cache
    /// yet (Android guards `listWorkspaces()` on an empty snapshot). A failure is
    /// swallowed: the bus snapshot on connect is the authoritative source.
    @discardableResult
    func seedIfEmpty() async -> [WorkspaceInfo] {
        guard currentWorkspaces.isEmpty else { return currentWorkspaces }
        guard let list = try? await agentAPI.getWorkspaces() else { return currentWorkspaces }
        setWorkspaces(list)
        return list
    }

    // MARK: - Mutations (no local patch — the `projects.changed` echo reconciles)

    @discardableResult
    func createWorkspace(_ body: WorkspaceWrite) async throws -> WorkspaceInfo {
        try await mapConflict { try await agentAPI.createWorkspace(body) }
    }

    @discardableResult
    func updateWorkspace(currentName: String, body: WorkspaceUpdate) async throws -> WorkspaceInfo {
        try await mapConflict { try await agentAPI.updateWorkspace(currentName: currentName, update: body) }
    }

    func deleteWorkspace(name: String) async throws {
        try await mapConflict { try await agentAPI.deleteWorkspace(name: name) }
    }

    @discardableResult
    func reorderWorkspaces(_ names: [String]) async throws -> [WorkspaceInfo] {
        try await mapConflict { try await agentAPI.reorderWorkspaces(WorkspaceOrderRequest(names: names)) }
    }

    // MARK: - Linear metadata (form auto-scan filter subform)

    func linearLabels(workspaceName: String, forceRefresh: Bool) async throws -> [LinearLabelDto] {
        if !forceRefresh, let cached = labelCache[workspaceName] { return cached }
        let labels = try await agentAPI.getLinearLabels(workspaceName: workspaceName)
        labelCache[workspaceName] = labels
        return labels
    }

    func linearStates(workspaceName: String, forceRefresh: Bool) async throws -> [LinearStateDto] {
        if !forceRefresh, let cached = stateCache[workspaceName] { return cached }
        let states = try await agentAPI.getLinearStates(workspaceName: workspaceName)
        stateCache[workspaceName] = states
        return states
    }

    private func mapConflict<T>(_ work: () async throws -> T) async throws -> T {
        do {
            return try await work()
        } catch let error as APIError where error.code == 409 {
            throw WorkspaceConflictError(detail: ConflictDetail.string(from: error.errorBody))
        }
    }
}
