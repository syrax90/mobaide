import XCTest
@testable import Mobaide

final class TokenStoreTests: XCTestCase {
    private func makeStore(now: Date = Date()) -> (TokenStore, InMemoryKeychainStore) {
        let keychain = InMemoryKeychainStore()
        return (TokenStore(keychain: keychain, now: { now }), keychain)
    }

    func testSaveAgentUrlTrimsTrailingSlashes() {
        let (store, _) = makeStore()
        store.saveAgentUrl("https://agent.example.com///")
        XCTAssertEqual(store.getAgentUrl(), "https://agent.example.com")
    }

    func testConnectionModeDefaultsToDirect() {
        let (store, keychain) = makeStore()
        XCTAssertEqual(store.getConnectionMode(), .direct)
        keychain.set("relay", forKey: TokenStore.keyConnectionMode)
        XCTAssertEqual(store.getConnectionMode(), .relay)
        keychain.set("anything-else", forKey: TokenStore.keyConnectionMode)
        XCTAssertEqual(store.getConnectionMode(), .direct)
    }

    func testSaveRelayCredentialsSetsEverything() {
        let (store, _) = makeStore()
        store.saveRelayCredentials(
            serverUrl: "https://relay.example.com/",
            agentId: "A1",
            pairingId: "P1",
            deviceId: "D1",
            accessToken: "at",
            refreshToken: "rt"
        )
        XCTAssertEqual(store.getConnectionMode(), .relay)
        XCTAssertEqual(store.getRelayServerUrl(), "https://relay.example.com")
        XCTAssertEqual(store.getAgentId(), "A1")
        XCTAssertEqual(store.getPairingId(), "P1")
        XCTAssertEqual(store.getDeviceId(), "D1")
        XCTAssertEqual(store.getAccessToken(), "at")
        XCTAssertEqual(store.getRefreshToken(), "rt")
    }

    func testEffectiveBaseUrl() {
        let (direct, _) = makeStore()
        XCTAssertNil(direct.getEffectiveBaseUrl())
        direct.saveAgentUrl("https://agent.example.com")
        XCTAssertEqual(direct.getEffectiveBaseUrl(), "https://agent.example.com")

        let (relay, _) = makeStore()
        relay.saveRelayCredentials(
            serverUrl: "https://relay.example.com",
            agentId: "A1",
            pairingId: "P1",
            deviceId: "D1",
            accessToken: "at",
            refreshToken: "rt"
        )
        XCTAssertEqual(relay.getEffectiveBaseUrl(), "https://relay.example.com/proxy/A1")
    }

    func testAgentScopeKeyPrefersTheAgentIdAndFallsBackToTheAgentUrl() {
        let (store, keychain) = makeStore()
        XCTAssertNil(store.getAgentScopeKey(), "a device that reached no agent has no per-agent bucket")

        store.saveAgentUrl("https://agent.example.com///")
        XCTAssertEqual(
            store.getAgentScopeKey(), "https://agent.example.com",
            "DIRECT mode is never issued an agent id, so the stored (trimmed) URL carries the scope"
        )

        store.saveRelayCredentials(
            serverUrl: "https://relay.example.com",
            agentId: "A1",
            pairingId: "P1",
            deviceId: "D1",
            accessToken: "at",
            refreshToken: "rt"
        )
        XCTAssertEqual(
            store.getAgentScopeKey(), "A1",
            "a paired agent keeps one bucket even though a direct URL is still on file"
        )

        keychain.removeValue(forKey: TokenStore.keyAgentId)
        XCTAssertEqual(store.getAgentScopeKey(), "https://agent.example.com")
    }

    func testIsLoggedInPerMode() {
        let (direct, _) = makeStore()
        XCTAssertFalse(direct.isLoggedIn())
        direct.saveTokens(accessToken: "a", refreshToken: "r")
        XCTAssertFalse(direct.isLoggedIn(), "direct mode also requires the agent URL")
        direct.saveAgentUrl("https://agent.example.com")
        XCTAssertTrue(direct.isLoggedIn())

        let (relay, relayKeychain) = makeStore()
        relay.saveRelayCredentials(
            serverUrl: "https://relay.example.com",
            agentId: "A1",
            pairingId: "P1",
            deviceId: "D1",
            accessToken: "a",
            refreshToken: "r"
        )
        XCTAssertTrue(relay.isLoggedIn())
        // Pairing/device ids are NOT required for relay login state.
        relayKeychain.removeValue(forKey: TokenStore.keyPairingId)
        relayKeychain.removeValue(forKey: TokenStore.keyDeviceId)
        XCTAssertTrue(relay.isLoggedIn())
        relayKeychain.removeValue(forKey: TokenStore.keyAgentId)
        XCTAssertFalse(relay.isLoggedIn())
    }

    func testClearWipesTheWholeStore() {
        let (store, _) = makeStore()
        store.saveAgentUrl("https://agent.example.com")
        store.saveTokens(accessToken: "a", refreshToken: "r")
        store.clear()
        XCTAssertNil(store.getAgentUrl())
        XCTAssertNil(store.getAccessToken())
        XCTAssertNil(store.getRefreshToken())
    }

    func testExpiringSoonMatrix() {
        let now = Date()
        let nowSeconds = Int64(now.timeIntervalSince1970)
        let (store, _) = makeStore(now: now)

        XCTAssertTrue(store.isAccessTokenExpiringSoon(), "no token counts as expiring")

        store.saveTokens(accessToken: "garbage", refreshToken: "r")
        XCTAssertTrue(store.isAccessTokenExpiringSoon(), "unparseable token counts as expiring")

        store.saveTokens(accessToken: TestJWT.token(exp: nowSeconds + 3600), refreshToken: "r")
        XCTAssertFalse(store.isAccessTokenExpiringSoon())

        store.saveTokens(accessToken: TestJWT.token(exp: nowSeconds + 59), refreshToken: "r")
        XCTAssertTrue(store.isAccessTokenExpiringSoon(), "60 s leeway")

        store.saveTokens(accessToken: TestJWT.token(exp: nowSeconds + 61), refreshToken: "r")
        XCTAssertFalse(store.isAccessTokenExpiringSoon())

        store.saveTokens(accessToken: TestJWT.token(exp: nowSeconds - 100), refreshToken: "r")
        XCTAssertTrue(store.isAccessTokenExpiringSoon())

        store.saveTokens(accessToken: TestJWT.token(exp: nil), refreshToken: "r")
        XCTAssertFalse(store.isAccessTokenExpiringSoon(), "no exp claim means the token never expires")
    }
}
