import XCTest
@testable import Mobaide

final class TokenStoreTests: XCTestCase {
    private func makeStore(now: Date = Date()) -> (TokenStore, InMemoryKeychainStore) {
        let keychain = InMemoryKeychainStore()
        return (TokenStore(keychain: keychain, now: { now }), keychain)
    }

    private func saveRelayCredentials(_ store: TokenStore, serverUrl: String = "https://relay.example.com") {
        store.saveRelayCredentials(
            serverUrl: serverUrl,
            agentId: "A1",
            pairingId: "P1",
            deviceId: "D1",
            accessToken: "at",
            refreshToken: "rt"
        )
    }

    /// What an app version with the removed direct connection mode left in
    /// the keychain: an agent URL and tokens, and no relay connection.
    private func seedCredentialsWithoutRelay(_ store: TokenStore, _ keychain: InMemoryKeychainStore) {
        keychain.set("https://agent.example.com", forKey: TokenStore.keyAgentUrl)
        store.saveTokens(accessToken: "direct-access", refreshToken: "direct-refresh")
    }

    func testSaveRelayCredentialsSetsEverything() {
        let (store, _) = makeStore()
        saveRelayCredentials(store, serverUrl: "https://relay.example.com/")
        XCTAssertEqual(store.getRelayServerUrl(), "https://relay.example.com")
        XCTAssertEqual(store.getAgentId(), "A1")
        XCTAssertEqual(store.getPairingId(), "P1")
        XCTAssertEqual(store.getDeviceId(), "D1")
        XCTAssertEqual(store.getAccessToken(), "at")
        XCTAssertEqual(store.getRefreshToken(), "rt")
    }

    func testEffectiveBaseUrlIsTheRelayProxyOfTheAgent() {
        let (store, _) = makeStore()
        XCTAssertNil(store.getEffectiveBaseUrl())

        saveRelayCredentials(store)
        XCTAssertEqual(store.getEffectiveBaseUrl(), "https://relay.example.com/proxy/A1")
    }

    func testAgentScopeKeyIsTheAgentId() {
        let (store, keychain) = makeStore()
        XCTAssertNil(store.getAgentScopeKey(), "a device that reached no agent has no per-agent bucket")

        saveRelayCredentials(store)
        XCTAssertEqual(store.getAgentScopeKey(), "A1")

        keychain.removeValue(forKey: TokenStore.keyAgentId)
        XCTAssertNil(store.getAgentScopeKey())
    }

    func testIsLoggedInNeedsARelayConnection() {
        let (stale, staleKeychain) = makeStore()
        XCTAssertFalse(stale.isLoggedIn())
        seedCredentialsWithoutRelay(stale, staleKeychain)
        XCTAssertFalse(stale.isLoggedIn(), "tokens without a relay connection cannot reach an agent")

        let (relay, relayKeychain) = makeStore()
        saveRelayCredentials(relay)
        XCTAssertTrue(relay.isLoggedIn())
        // Pairing/device ids are NOT required for relay login state.
        relayKeychain.removeValue(forKey: TokenStore.keyPairingId)
        relayKeychain.removeValue(forKey: TokenStore.keyDeviceId)
        XCTAssertTrue(relay.isLoggedIn())
        relayKeychain.removeValue(forKey: TokenStore.keyAgentId)
        XCTAssertFalse(relay.isLoggedIn())
    }

    func testClearCredentialsWithoutRelayRemovesThem() {
        let (store, keychain) = makeStore()
        seedCredentialsWithoutRelay(store, keychain)

        store.clearCredentialsWithoutRelay()

        XCTAssertNil(store.getAccessToken())
        XCTAssertNil(store.getRefreshToken())
        XCTAssertNil(keychain.string(forKey: TokenStore.keyAgentUrl))
    }

    func testClearCredentialsWithoutRelayKeepsARelayConnection() {
        let (store, _) = makeStore()
        saveRelayCredentials(store)

        store.clearCredentialsWithoutRelay()

        XCTAssertTrue(store.isLoggedIn())
        XCTAssertEqual(store.getAccessToken(), "at")
        XCTAssertEqual(store.getRefreshToken(), "rt")
        XCTAssertEqual(store.getEffectiveBaseUrl(), "https://relay.example.com/proxy/A1")
    }

    func testClearWipesTheWholeStore() {
        let (store, _) = makeStore()
        saveRelayCredentials(store)
        store.clear()
        XCTAssertNil(store.getRelayServerUrl())
        XCTAssertNil(store.getAgentId())
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
