import XCTest
@testable import Mobaide

final class SessionTokenAdapterTests: XCTestCase {
    private func adapter(for harness: SessionHarness) -> SessionAgentTokenProvider {
        SessionAgentTokenProvider(tokenStore: harness.tokenStore, sessionManager: harness.sessionManager)
    }

    func testValidTokenNilWhenNotFullyConnected() async {
        let harness = SessionHarness(userLoggedIn: true)
        let token = await adapter(for: harness).validToken()
        XCTAssertNil(token, "AgentAPI synthesizes the local 401 from a nil token")
    }

    func testValidTokenReturnsStoredTokenWithoutRefreshWhenFresh() async {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let token = await adapter(for: harness).validToken()
        XCTAssertEqual(token?.value, harness.tokenStore.getAccessToken())
        XCTAssertEqual(token?.justRefreshed, false)
        XCTAssertEqual(harness.refreshTransport.callCount, 0)
    }

    func testValidTokenPreemptivelyRefreshesExpiringToken() async {
        let now = Date()
        let harness = SessionHarness(
            userLoggedIn: true,
            agentConnected: true,
            accessTokenExp: Int64(now.timeIntervalSince1970) + 30,
            now: now
        )
        let token = await adapter(for: harness).validToken()
        XCTAssertEqual(harness.refreshTransport.callCount, 1)
        XCTAssertEqual(token?.value, "new-access")
        XCTAssertEqual(token?.justRefreshed, true, "suppresses the reactive 401 retry")
    }

    func testValidTokenNilOnTerminalRefresh() async {
        let now = Date()
        let transport = FakeRefreshTransport(behavior: .respond(status: 401, body: ""))
        let harness = SessionHarness(
            userLoggedIn: true,
            agentConnected: true,
            accessTokenExp: Int64(now.timeIntervalSince1970) + 30,
            transport: transport,
            now: now
        )
        let token = await adapter(for: harness).validToken()
        XCTAssertNil(token)
    }

    func testValidTokenKeepsStaleTokenOnTransientRefresh() async {
        let now = Date()
        let transport = FakeRefreshTransport(behavior: .fail(URLError(.timedOut)))
        let harness = SessionHarness(
            userLoggedIn: true,
            agentConnected: true,
            accessTokenExp: Int64(now.timeIntervalSince1970) + 30,
            transport: transport,
            now: now
        )
        let stale = harness.tokenStore.getAccessToken()
        let token = await adapter(for: harness).validToken()
        XCTAssertEqual(token?.value, stale, "transient failure proceeds with the existing token")
        XCTAssertEqual(token?.justRefreshed, false)
    }

    func testRefreshedTokenReturnsNewAccessTokenOnSuccess() async {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let token = await adapter(for: harness).refreshedToken()
        XCTAssertEqual(token, "new-access")
    }

    func testRefreshedTokenNilOnFailure() async {
        let transport = FakeRefreshTransport(behavior: .respond(status: 500, body: ""))
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true, transport: transport)
        let token = await adapter(for: harness).refreshedToken()
        XCTAssertNil(token)
    }

    func testUserTokenProviderReadsIdentityStore() async {
        let harness = SessionHarness(userLoggedIn: true)
        let provider = SessionUserTokenProvider(userIdentityStore: harness.userIdentityStore)
        let token = await provider.userToken()
        XCTAssertEqual(token, "user-token")
    }

    func testSessionInvalidatorMapsToTokenRejected() async throws {
        let harness = SessionHarness(userLoggedIn: true)
        let events = StreamCollector(harness.sessionManager.sessionEvents())
        await SessionInvalidatorAdapter(sessionManager: harness.sessionManager).invalidateUserSession()
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .unauthenticated)
        let event = try await events.next()
        XCTAssertEqual(event, .tokenRejected)
    }
}
