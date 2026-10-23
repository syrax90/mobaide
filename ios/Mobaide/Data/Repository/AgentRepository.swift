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

    /// Cached agent for `id`, or nil when the account does not own one by that
    /// id. Callers that act on an id from outside the app must resolve it here
    /// first — the list is `GET /api/agents` under the user's own token.
    func agent(id: String) -> AgentSummaryResponse? {
        currentAgents.first { $0.agent_id == id }
    }

    /// Resolve `id`, refreshing once when it is not cached. Returning nil means
    /// the account genuinely does not own it; a refresh failure *throws* rather
    /// than reporting nil, so a caller cannot mistake "we could not find out"
    /// for "no such agent" — the cache is empty on every cold start, so that
    /// distinction carries the whole first lookup after launch.
    func resolveAgent(id: String) async throws -> AgentSummaryResponse? {
        if let cached = agent(id: id) { return cached }
        try await refreshAgents()
        return agent(id: id)
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
            last_seen_at: existing.last_seen_at,
            supports_uninstall: existing.supports_uninstall
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

    /// With `uninstall`, the agent also deletes itself from its host — at once
    /// when online, otherwise on its next connect within 30 days.
    func deleteAgent(agentId: String, uninstall: Bool = false) async throws {
        try await centralAPI.deleteAgent(userToken: try userToken(), agentId: agentId, uninstall: uninstall)
        // Remove the row locally first: a failed best-effort refresh must not
        // leave a stale, tappable row that would connect to a deleted agent
        // (there is no agent.deleted user event to self-heal the list).
        setAgents(currentAgents.filter { $0.agent_id != agentId })
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

    /// Mint a short pairing code to retype on the machine being paired.
    ///
    /// The reverse of `claimAgentByPairing`: nothing has to be copied *off* the
    /// agent host, which is what makes it work where a camera cannot reach.
    func mintDeviceCode() async throws -> DeviceCodeMintResponse {
        try await centralAPI.mintDeviceCode(userToken: try userToken())
    }

    func deviceCodeStatus(requestId: String) async throws -> DeviceCodeStatusResponse {
        try await centralAPI.getDeviceCodeStatus(userToken: try userToken(), requestId: requestId)
    }

    /// Approve a redeemed code; refreshes the agent list like a QR claim does.
    @discardableResult
    func confirmDeviceCode(
        requestId: String,
        request: DeviceCodeConfirmRequest
    ) async throws -> PairingClaimResponse {
        let response = try await centralAPI.confirmDeviceCode(
            userToken: try userToken(),
            requestId: requestId,
            request: request
        )
        // Best-effort for the same reason as the QR claim: the pairing already
        // succeeded server-side and the watcher refreshes again.
        try? await refreshAgents()
        return response
    }

    func cancelDeviceCode(requestId: String) async throws {
        try await centralAPI.cancelDeviceCode(userToken: try userToken(), requestId: requestId)
    }

    func trustPin() async throws -> TrustPinResponse {
        try await centralAPI.getTrustPin(userToken: try userToken())
    }

    private func userToken() throws -> String {
        guard let token = userIdentityStore.getUserAccessToken() else {
            throw APIError(code: -1, errorBody: "Not signed in")
        }
        return token
    }
}
