import XCTest
@testable import Mobaide

final class SessionManagerRefreshTests: XCTestCase {
    func testTwentyConcurrentCallersShareOneNetworkCall() async {
        let transport = FakeRefreshTransport()
        let gate = AsyncGate()
        transport.gate = gate
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true, transport: transport)

        let results = await withTaskGroup(of: RefreshResult.self, returning: [RefreshResult].self) { group in
            for _ in 0..<20 {
                group.addTask { await harness.sessionManager.refresh() }
            }
            // Let every caller reach the in-flight task before the network
            // responds.
            try? await Task.sleep(for: .milliseconds(100))
            gate.open()
            var collected: [RefreshResult] = []
            for await result in group {
                collected.append(result)
            }
            return collected
        }

        XCTAssertEqual(transport.callCount, 1, "single-flight: exactly one network refresh")
        XCTAssertEqual(results.count, 20)
        XCTAssertTrue(results.allSatisfy { $0 == .success })
        XCTAssertEqual(harness.tokenStore.getAccessToken(), "new-access")
        XCTAssertEqual(harness.tokenStore.getRefreshToken(), "new-refresh")
    }

    func testSuccessRotatesTokensAndPromotesState() async {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        await harness.sessionManager.agentDisconnect()
        harness.tokenStore.saveAgentUrl("https://agent.example.com")
        harness.tokenStore.saveTokens(accessToken: "old", refreshToken: "old-refresh")

        let result = await harness.sessionManager.refresh()

        XCTAssertEqual(result, .success)
        XCTAssertEqual(harness.tokenStore.getAccessToken(), "new-access")
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .fullyConnected)
    }

    func testNoRefreshTokenIsTerminalAndKeepsUserIdentity() async {
        let harness = SessionHarness(userLoggedIn: true)
        let result = await harness.sessionManager.refresh()

        XCTAssertEqual(result, .terminal)
        XCTAssertEqual(harness.refreshTransport.callCount, 0)
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly, "terminal refresh drops only the agent connection")
        XCTAssertTrue(harness.userIdentityStore.isUserLoggedIn())
    }

    func testMissingAgentUrlIsTerminal() async {
        let harness = SessionHarness()
        harness.tokenStore.saveTokens(accessToken: "a", refreshToken: "r")

        let result = await harness.sessionManager.refresh()

        XCTAssertEqual(result, .terminal)
        XCTAssertEqual(harness.refreshTransport.callCount, 0)
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .unauthenticated)
    }

    func testHTTP401IsTerminalAndClearsAgentTokens() async {
        let transport = FakeRefreshTransport(behavior: .respond(status: 401, body: ""))
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true, transport: transport)

        let result = await harness.sessionManager.refresh()

        XCTAssertEqual(result, .terminal)
        XCTAssertNil(harness.tokenStore.getAccessToken())
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly)
    }

    func testHTTP500IsTransientAndKeepsTokens() async {
        let transport = FakeRefreshTransport(behavior: .respond(status: 500, body: "oops"))
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true, transport: transport)

        let result = await harness.sessionManager.refresh()

        XCTAssertEqual(result, .transient)
        XCTAssertEqual(harness.tokenStore.getRefreshToken(), "refresh-1")
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .fullyConnected)
    }

    func testNetworkErrorIsTransient() async {
        let transport = FakeRefreshTransport(behavior: .fail(URLError(.notConnectedToInternet)))
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true, transport: transport)

        let result = await harness.sessionManager.refresh()

        XCTAssertEqual(result, .transient)
        XCTAssertEqual(harness.tokenStore.getRefreshToken(), "refresh-1")
    }

    func testEmptySuccessBodyIsTransient() async {
        let transport = FakeRefreshTransport(behavior: .respond(status: 200, body: ""))
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true, transport: transport)

        let result = await harness.sessionManager.refresh()

        XCTAssertEqual(result, .transient)
        XCTAssertEqual(harness.tokenStore.getRefreshToken(), "refresh-1")
    }

    func testUndecodableSuccessBodyIsTransient() async {
        let transport = FakeRefreshTransport(behavior: .respond(status: 200, body: "not-json"))
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true, transport: transport)

        let result = await harness.sessionManager.refresh()

        XCTAssertEqual(result, .transient)
    }

    func testSuccessArrivingAfterLogoutIsTerminal() async {
        let transport = FakeRefreshTransport()
        let gate = AsyncGate()
        transport.gate = gate
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true, transport: transport)

        let refreshTask = Task { await harness.sessionManager.refresh() }
        // Wait for the network call to be in flight, then kill the session.
        while transport.callCount == 0 {
            try? await Task.sleep(for: .milliseconds(1))
        }
        await harness.sessionManager.userLogout()
        gate.open()

        let result = await refreshTask.value
        XCTAssertEqual(result, .terminal, "a killed session must not be resurrected by a late 2xx")
        XCTAssertNil(harness.tokenStore.getAccessToken())
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .unauthenticated)
    }

    func testStaleSuccessAfterAgentDisconnectDoesNotRepromote() async {
        let transport = FakeRefreshTransport()
        let gate = AsyncGate()
        transport.gate = gate
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true, transport: transport)

        let refreshTask = Task { await harness.sessionManager.refresh() }
        while transport.callCount == 0 {
            try? await Task.sleep(for: .milliseconds(1))
        }
        await harness.sessionManager.agentDisconnect()
        gate.open()

        let result = await refreshTask.value
        XCTAssertEqual(result, .terminal, "a superseded refresh surfaces as terminal")
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly, "a stale 2xx must not re-promote past an intentional disconnect")
        XCTAssertNil(harness.tokenStore.getAccessToken(), "stale tokens must not be re-saved")
    }

    func testStaleTerminalAfterIncompatibleServerKeepsTakeoverState() async {
        let transport = FakeRefreshTransport(behavior: .respond(status: 401, body: ""))
        let gate = AsyncGate()
        transport.gate = gate
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true, transport: transport)
        let info = AuthState.IncompatibleServer(
            reason: "protocol too old",
            minClientVersion: 7,
            storeUrl: "",
            storeFallbackUrl: ""
        )

        let refreshTask = Task { await harness.sessionManager.refresh() }
        while transport.callCount == 0 {
            try? await Task.sleep(for: .milliseconds(1))
        }
        await harness.sessionManager.markIncompatibleServer(info)
        gate.open()

        let result = await refreshTask.value
        XCTAssertEqual(result, .terminal)
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .incompatibleServer(info), "a stale 401 must not demote the upgrade takeover")
    }

    func testDirectModeUsesAgentRefreshPath() async {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        _ = await harness.sessionManager.refresh()
        XCTAssertEqual(
            harness.refreshTransport.requests.first?.url?.absoluteString,
            "https://agent.example.com/api/auth/refresh"
        )
    }

    func testRelayModeUsesMobileRefreshPath() async {
        let harness = SessionHarness(userLoggedIn: true)
        harness.tokenStore.saveRelayCredentials(
            serverUrl: "https://relay.example.com/",
            agentId: "A1",
            pairingId: "P1",
            deviceId: "D1",
            accessToken: "a",
            refreshToken: "r"
        )

        _ = await harness.sessionManager.refresh()

        XCTAssertEqual(
            harness.refreshTransport.requests.first?.url?.absoluteString,
            "https://relay.example.com/api/mobile/refresh"
        )
    }

    func testSequentialRefreshesEachHitTheNetwork() async {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        _ = await harness.sessionManager.refresh()
        harness.tokenStore.saveTokens(accessToken: "a2", refreshToken: "r2")
        _ = await harness.sessionManager.refresh()
        XCTAssertEqual(harness.refreshTransport.callCount, 2, "the in-flight slot must clear after completion")
    }
}
