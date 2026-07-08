import Foundation

/// App-scoped live agent list (Android `AgentRepository`). The observable
/// cache is the merge of REST refreshes and `agent.status_changed` pushes that
/// `UserEventBus` routes here through `AgentStatusApplying`; whichever source
/// flips an agent online first is what the pairing watcher observes. Mutations
/// call the central server; create/delete/claim reconcile with a full refresh.
@MainActor
final class AgentRepository: AgentStatusApplying {
    private let centralAPI: CentralAPI
    private let userIdentityStore: UserIdentityStore
    private let broadcaster = Broadcaster<[AgentSummaryResponse]>.stateFlow()

    private(set) var currentAgents: [AgentSummaryResponse] = []

    init(centralAPI: CentralAPI, userIdentityStore: UserIdentityStore) {
        self.centralAPI = centralAPI
        self.userIdentityStore = userIdentityStore
        broadcaster.emit([])
    }

    func agents() -> AsyncStream<[AgentSummaryResponse]> {
        broadcaster.subscribe()
    }

    private func setAgents(_ list: [AgentSummaryResponse]) {
        currentAgents = list
        broadcaster.emit(list)
    }

    // MARK: - AgentStatusApplying

    func refreshAgents() async throws {
        let list = try await centralAPI.listAgents(userToken: try userToken())
        setAgents(list)
    }

    func clear() async {
        setAgents([])
    }

    /// Patch one agent's `online` flag in place. No-op when the agent is not
    /// cached or the flag is unchanged (Android parity — avoids spurious emits).
    func updateAgentOnline(agentId: String, online: Bool) async {
        guard let index = currentAgents.firstIndex(where: { $0.agent_id == agentId }),
              currentAgents[index].online != online
        else { return }
        var list = currentAgents
        let existing = list[index]
        list[index] = AgentSummaryResponse(
            agent_id: existing.agent_id,
            name: existing.name,
            online: online,
            created_at: existing.created_at,
            last_seen_at: existing.last_seen_at
        )
        setAgents(list)
    }

    // MARK: - Mutations

    @discardableResult
    func createAgent(name: String) async throws -> CreateAgentResponse {
        let response = try await centralAPI.createAgent(userToken: try userToken(), name: name)
        // Best-effort: a failed list refresh must never discard the one-time
        // token in `response` (the bus/next refresh reconciles the list).
        try? await refreshAgents()
        return response
    }

    /// Rotates the agent's credentials only; the list row is unchanged, so no
    /// refresh (Android parity).
    @discardableResult
    func reregisterAgent(agentId: String) async throws -> ReRegisterAgentResponse {
        try await centralAPI.reregisterAgent(userToken: try userToken(), agentId: agentId)
    }

    func deleteAgent(agentId: String) async throws {
        try await centralAPI.deleteAgent(userToken: try userToken(), agentId: agentId)
        // Best-effort: the delete already succeeded server-side, so a failed
        // refresh must not surface a false "delete failed" (no agent.deleted
        // user event self-heals the list).
        try? await refreshAgents()
    }

    @discardableResult
    func claimAgentByPairing(request: PairingClaimRequest) async throws -> PairingClaimResponse {
        let response = try await centralAPI.claimAgentByPairing(userToken: try userToken(), request: request)
        // Best-effort: the claim already succeeded server-side and the pairing
        // watcher refreshes again, so a failed refresh must not fail the claim.
        try? await refreshAgents()
        return response
    }

    private func userToken() throws -> String {
        guard let token = userIdentityStore.getUserAccessToken() else {
            throw APIError(code: -1, errorBody: "Not signed in")
        }
        return token
    }
}
