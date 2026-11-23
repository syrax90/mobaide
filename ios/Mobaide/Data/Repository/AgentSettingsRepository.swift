import Foundation
import Observation

/// The connected agent's own settings. Reconciled by `agent_settings.changed`,
/// a full snapshot the agent also republishes on every events-channel
/// connect, so an agent switch needs no REST refresh; leaving the connected
/// state clears it.
@MainActor
@Observable
final class AgentSettingsRepository {
    private(set) var settings: AgentSettingsResponse?

    @ObservationIgnored private let agentAPI: AgentAPI
    @ObservationIgnored private let agentEventBus: AgentEventBus
    @ObservationIgnored private let sessionManager: SessionManager
    @ObservationIgnored private var subscriptions: [Task<Void, Never>] = []
    /// Bumped by every `agent_settings.changed`, so an older answer never overwrites one.
    @ObservationIgnored private var events = 0

    init(agentAPI: AgentAPI, agentEventBus: AgentEventBus, sessionManager: SessionManager) {
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        self.sessionManager = sessionManager
    }

    /// Idempotent. Subscribe before `AgentEventBus.start()` so the snapshot the
    /// bus re-pushes on connect is not missed (the events stream has no replay).
    func start() {
        guard subscriptions.isEmpty else { return }
        let events = agentEventBus.events()
        subscriptions.append(Task { [weak self] in
            for await event in events {
                if case .agentSettingsChanged(let settings) = event {
                    self?.events += 1
                    self?.settings = settings
                }
            }
        })
        let states = sessionManager.authStates()
        subscriptions.append(Task { [weak self] in
            for await state in states where state != .fullyConnected {
                self?.settings = nil
            }
        })
    }

    /// `capabilities.agent_settings.keep_awake` of the connected agent.
    func keepAwakeSupported() async throws -> Bool {
        try await agentAPI.getAgentVersion().keepAwakeCapability
    }

    /// A snapshot that arrived while this request was in flight is newer and wins.
    @discardableResult
    func refresh() async throws -> AgentSettingsResponse {
        let before = events
        let answer = try await agentAPI.getAgentSettings()
        if events == before { settings = answer }
        return answer
    }

    /// Applied on the agent before it answers, for every client of it.
    @discardableResult
    func setKeepAwake(_ enabled: Bool) async throws -> AgentSettingsResponse {
        let before = events
        let answer = try await agentAPI.setKeepAwake(enabled: enabled)
        if events == before { settings = answer }
        return answer
    }
}
