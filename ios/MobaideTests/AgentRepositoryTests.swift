import XCTest
@testable import Mobaide

@MainActor
final class AgentRepositoryTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeRepo() -> AgentRepository {
        AgentHarness().agentRepository
    }

    private func agentsJSON(id: String, online: Bool) -> String {
        #"[{"agent_id":"\#(id)","name":"Agent","online":\#(online),"created_at":"t"}]"#
    }

    func testRefreshReplacesCache() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: true))
        try await repo.refreshAgents()
        XCTAssertEqual(repo.currentAgents.map(\.agent_id), ["a1"])
        XCTAssertEqual(repo.currentAgents.first?.online, true)
    }

    func testUpdateAgentOnlinePatchesInPlace() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))
        try await repo.refreshAgents()
        await repo.updateAgentOnline(agentId: "a1", online: true)
        XCTAssertEqual(repo.currentAgents.first?.online, true)
    }

    func testCreateRefreshesAfter() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: #"{"agent_id":"a1","name":"A","token":"tok"}"#)
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))
        let created = try await repo.createAgent(name: "A")
        XCTAssertEqual(created.token, "tok")
        XCTAssertEqual(repo.currentAgents.map(\.agent_id), ["a1"])
        XCTAssertEqual(StubURLProtocol.requests.count, 2)
    }

    func testReregisterDoesNotRefresh() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: #"{"agent_id":"a1","token":"tok2"}"#)
        let response = try await repo.reregisterAgent(agentId: "a1")
        XCTAssertEqual(response.token, "tok2")
        XCTAssertEqual(StubURLProtocol.requests.count, 1)
    }

    func testClaimRefreshesAfter() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: #"{"agent_id":"a1","agent_friendly_name":"A","ws_delivered":true}"#)
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: true))
        let response = try await repo.claimAgentByPairing(
            request: PairingClaimRequest(nonce: "n", trust_key_fp: "f", mobile_device_id: "d")
        )
        XCTAssertEqual(response.agent_id, "a1")
        XCTAssertEqual(repo.currentAgents.first?.online, true)
    }

    func testCreateReturnsTokenEvenWhenRefreshFails() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: #"{"agent_id":"a1","name":"A","token":"tok"}"#)  // create ok
        StubURLProtocol.enqueue(status: 500, body: "boom")                                            // refresh fails
        let created = try await repo.createAgent(name: "A")
        XCTAssertEqual(created.token, "tok")  // one-time token preserved despite refresh failure
    }

    func testDeleteRemovesRowLocallyEvenWhenRefreshFails() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: true))
        try await repo.refreshAgents()
        XCTAssertEqual(repo.currentAgents.count, 1)

        StubURLProtocol.enqueue(status: 204, body: "")     // DELETE ok
        StubURLProtocol.enqueue(status: 500, body: "boom")  // best-effort refresh fails
        try await repo.deleteAgent(agentId: "a1")
        XCTAssertTrue(repo.currentAgents.isEmpty)  // removed locally, no stale tappable row
    }

    func testRefreshDropsResponseWhenAccountSwitchesMidFlight() async throws {
        let session = SessionHarness(userLoggedIn: true)
        let repo = AgentHarness(session: session).agentRepository
        let identity = session.userIdentityStore
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: true))
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "user-token-2",
                email: "other@example.com",
                userId: "u2",
                role: "user"
            )
        }

        try await repo.refreshAgents()

        XCTAssertTrue(
            repo.currentAgents.isEmpty,
            "a list landing after an account switch must not show the previous account's agents"
        )
    }

    func testRefreshAppliesResponseWhenTokenRotatesForSameUserMidFlight() async throws {
        let session = SessionHarness(userLoggedIn: true)
        let repo = AgentHarness(session: session).agentRepository
        let identity = session.userIdentityStore
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: true))
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "rotated-token",
                email: "user@example.com",
                userId: "u1",
                role: "user"
            )
        }

        try await repo.refreshAgents()

        XCTAssertEqual(repo.currentAgents.map(\.agent_id), ["a1"], "the guard keys on user id, not on the token")
    }
}
