import Foundation
import Observation

/// State for the terminal's sessions drawer: the connected agent's live
/// session list plus the account's other agents. Every source is a bus-fed
/// repository cache, so the drawer needs no fetch of its own beyond a
/// best-effort agents refresh at creation.
@MainActor
@Observable
final class SessionsPanelModel {
    private(set) var sessions: [SessionResponse] = []
    /// False until the connected agent has pushed its snapshot over a live
    /// bus; the panel shows "Reconnecting…" instead of an empty list meanwhile.
    private(set) var synced = false
    private(set) var customTypeNames: [String: String] = [:]
    private(set) var agents: [AgentSummaryResponse] = []
    /// The session a panel-started stop is waiting on, if any; every row's
    /// stop control is disabled while it is set.
    private(set) var stoppingSessionId: String?
    let connectedAgentId: String?

    private let sessionRepository: SessionRepository
    private let agentEventBus: AgentEventBus
    private let customAgentTypesRepository: CustomAgentTypesRepository
    private let agentRepository: AgentRepository
    private var listSynced = false
    private var busConnected = false

    init(
        sessionRepository: SessionRepository,
        agentEventBus: AgentEventBus,
        customAgentTypesRepository: CustomAgentTypesRepository,
        agentRepository: AgentRepository,
        connectedAgentId: String?
    ) {
        self.sessionRepository = sessionRepository
        self.agentEventBus = agentEventBus
        self.customAgentTypesRepository = customAgentTypesRepository
        self.agentRepository = agentRepository
        self.connectedAgentId = connectedAgentId
        sessions = sessionRepository.currentSessions
        listSynced = sessionRepository.sessionsSynced
        customTypeNames = Self.typeNames(customAgentTypesRepository.currentTypes)
        agents = agentRepository.currentAgents
    }

    var connectedAgent: AgentSummaryResponse? {
        agents.first { $0.agent_id == connectedAgentId }
    }

    var otherAgents: [AgentSummaryResponse] {
        agents.filter { $0.agent_id != connectedAgentId }
    }

    func observeSessions() async {
        for await list in sessionRepository.sessions() {
            sessions = list
        }
    }

    // A snapshot from a dropped tunnel is not agent-authoritative (see
    // `SessionRepository`), so the list reads as synced only while the bus is up.
    func observeSessionsSynced() async {
        for await flag in sessionRepository.sessionsSyncedStates() {
            listSynced = flag
            synced = listSynced && busConnected
        }
    }

    func observeConnection() async {
        for await state in agentEventBus.connectionStates() {
            busConnected = state == .connected
            synced = listSynced && busConnected
        }
    }

    func observeCustomTypes() async {
        for await types in customAgentTypesRepository.types() {
            customTypeNames = Self.typeNames(types)
        }
    }

    func observeAgents() async {
        for await list in agentRepository.agents() {
            agents = list
        }
    }

    struct StoppedSession: Equatable {
        /// The open session to show instead when the stopped one is on screen,
        /// or nil when none is left.
        let nextSessionId: String?
    }

    /// The row disappears through the agent's `sessions.changed` push, so a
    /// success needs no refetch here. One stop at a time: the panel disables
    /// every stop control while one is in flight, and a second call returns
    /// nil. A 404 means the session is already gone, so it counts as stopped.
    func stopSession(_ id: String, force: Bool) async throws -> StoppedSession? {
        guard stoppingSessionId == nil else { return nil }
        stoppingSessionId = id
        defer { stoppingSessionId = nil }
        // Positions are read before the request: the agent's push can drop the
        // stopped row before the stop resolves.
        let snapshot = sessionRepository.currentSessions
        do {
            try await sessionRepository.deleteSession(id: id, force: force)
        } catch let error as APIError where error.code == 404 {}
        return StoppedSession(nextSessionId: Self.nearestOpenSessionId(
            snapshot: snapshot,
            current: sessionRepository.currentSessions,
            stoppedId: id
        ))
    }

    /// The closest open session to the stopped row — below first, then above,
    /// widening outward. Positions come from `snapshot`, the list as it was
    /// when the stop was confirmed; a candidate must still be open in `current`.
    static func nearestOpenSessionId(
        snapshot: [SessionResponse],
        current: [SessionResponse],
        stoppedId: String
    ) -> String? {
        guard let index = snapshot.firstIndex(where: { $0.session_id == stoppedId }) else { return nil }
        let open = Set(current.filter { SessionDisplay.canStop($0.state) }.map(\.session_id))
        for distance in 1..<snapshot.count {
            for candidate in [index + distance, index - distance] where snapshot.indices.contains(candidate) {
                let id = snapshot[candidate].session_id
                if open.contains(id) { return id }
            }
        }
        return nil
    }

    /// Best effort: the cached list from the agents screen still renders.
    func refreshAgents() async {
        try? await agentRepository.refreshAgents()
    }

    private static func typeNames(_ types: [CustomAgentTypeDto]) -> [String: String] {
        Dictionary(types.map { ("custom_\($0.id)", $0.name) }, uniquingKeysWith: { _, last in last })
    }
}
