import Foundation

/// App-scoped live project list (Android `ProjectsRepository`). Push-driven off
/// the agent event bus: every `projects.changed` carries the full authoritative
/// snapshot, so mutations never patch local state — they call REST and the bus
/// echo reconciles the cache. REST seeds the cache only while it is still empty.
@MainActor
final class ProjectsRepository {
    private let agentAPI: AgentAPI
    private let agentEventBus: AgentEventBus
    private let broadcaster = Broadcaster<[ProjectInfo]>.stateFlow()

    private(set) var currentProjects: [ProjectInfo] = []
    private var subscription: Task<Void, Never>?

    // In-session Linear metadata cache keyed by project name, feeding the form's
    // auto-scan filter subform so reopening a project does not refetch.
    private var labelCache: [String: [LinearLabelDto]] = [:]
    private var stateCache: [String: [LinearStateDto]] = [:]

    init(agentAPI: AgentAPI, agentEventBus: AgentEventBus) {
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        broadcaster.emit([])
    }

    func projects() -> AsyncStream<[ProjectInfo]> {
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
        guard case .projectsChanged(let list) = event else { return }
        setProjects(list)
    }

    private func setProjects(_ list: [ProjectInfo]) {
        currentProjects = list
        broadcaster.emit(list)
    }

    /// One-shot REST seed, used only when the bus has not populated the cache
    /// yet (Android guards `listProjects()` on an empty snapshot). A failure is
    /// swallowed: the bus snapshot on connect is the authoritative source.
    @discardableResult
    func seedIfEmpty() async -> [ProjectInfo] {
        guard currentProjects.isEmpty else { return currentProjects }
        guard let list = try? await agentAPI.getProjects() else { return currentProjects }
        setProjects(list)
        return list
    }

    // MARK: - Mutations (no local patch — the `projects.changed` echo reconciles)

    @discardableResult
    func createProject(_ body: ProjectWrite) async throws -> ProjectInfo {
        try await mapConflict { try await agentAPI.createProject(body) }
    }

    @discardableResult
    func updateProject(currentName: String, body: ProjectUpdate) async throws -> ProjectInfo {
        try await mapConflict { try await agentAPI.updateProject(currentName: currentName, update: body) }
    }

    func deleteProject(name: String) async throws {
        try await mapConflict { try await agentAPI.deleteProject(name: name) }
    }

    @discardableResult
    func reorderProjects(_ names: [String]) async throws -> [ProjectInfo] {
        try await mapConflict { try await agentAPI.reorderProjects(ProjectOrderRequest(names: names)) }
    }

    // MARK: - Linear metadata (form auto-scan filter subform)

    func linearLabels(projectName: String, forceRefresh: Bool) async throws -> [LinearLabelDto] {
        if !forceRefresh, let cached = labelCache[projectName] { return cached }
        let labels = try await agentAPI.getLinearLabels(projectName: projectName)
        labelCache[projectName] = labels
        return labels
    }

    func linearStates(projectName: String, forceRefresh: Bool) async throws -> [LinearStateDto] {
        if !forceRefresh, let cached = stateCache[projectName] { return cached }
        let states = try await agentAPI.getLinearStates(projectName: projectName)
        stateCache[projectName] = states
        return states
    }

    private func mapConflict<T>(_ work: () async throws -> T) async throws -> T {
        do {
            return try await work()
        } catch let error as APIError where error.code == 409 {
            throw ProjectConflictError(detail: ConflictDetail.string(from: error.errorBody))
        }
    }
}
