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

    /// Best effort: the cached list from the agents screen still renders.
    func refreshAgents() async {
        try? await agentRepository.refreshAgents()
    }

    private static func typeNames(_ types: [CustomAgentTypeDto]) -> [String: String] {
        Dictionary(types.map { ("custom_\($0.id)", $0.name) }, uniquingKeysWith: { _, last in last })
    }
}
