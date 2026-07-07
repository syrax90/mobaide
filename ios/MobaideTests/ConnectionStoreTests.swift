import XCTest
@testable import Mobaide

final class ConnectionStoreTests: XCTestCase {
    private func makeStores() -> (ConnectionStore, TokenStore) {
        (
            ConnectionStore(keychain: InMemoryKeychainStore()),
            TokenStore(keychain: InMemoryKeychainStore())
        )
    }

    func testUpsertAndRemoveProfiles() {
        let (store, _) = makeStores()
        XCTAssertEqual(store.listProfiles(), [])

        let profile = ConnectionStore.ConnectionProfile(
            id: "p1", label: "One", mode: "direct", agentUrl: "https://a.example.com",
            accessToken: "at", refreshToken: "rt", createdAt: 1
        )
        store.saveProfile(profile)
        store.setActiveProfileId("p1")
        XCTAssertEqual(store.listProfiles().count, 1)
        XCTAssertEqual(store.getActiveProfileId(), "p1")

        let renamed = ConnectionStore.ConnectionProfile(
            id: "p1", label: "Renamed", mode: "direct", agentUrl: "https://a.example.com",
            accessToken: "at", refreshToken: "rt", createdAt: 1
        )
        store.saveProfile(renamed)
        XCTAssertEqual(store.listProfiles().count, 1, "same id upserts")
        XCTAssertEqual(store.listProfiles().first?.label, "Renamed")

        store.removeProfile(id: "p1")
        XCTAssertEqual(store.listProfiles(), [])
        XCTAssertNil(store.getActiveProfileId(), "removing the active profile clears the active id")
    }

    func testProfileJSONUsesCamelCaseKeys() throws {
        let profile = ConnectionStore.ConnectionProfile(
            id: "p1", label: "One", mode: "relay", relayServerUrl: "https://r.example.com",
            agentId: "A1", createdAt: 42
        )
        let json = String(decoding: try JSONEncoder().encode([profile]), as: UTF8.self)
        XCTAssertTrue(json.contains("\"relayServerUrl\""), "Android kotlinx uses property-name keys: \(json)")
        XCTAssertTrue(json.contains("\"agentId\""))
        XCTAssertTrue(json.contains("\"createdAt\""))
    }

    func testProfileDecodeFillsMissingCreatedAt() throws {
        let json = #"[{"id":"p1","label":"L","mode":"direct"}]"#
        let profiles = try JSONDecoder().decode([ConnectionStore.ConnectionProfile].self, from: Data(json.utf8))
        XCTAssertEqual(profiles.first?.id, "p1")
        XCTAssertNotNil(profiles.first?.createdAt)
    }

    func testCaptureActiveProfileSnapshotsTokenStore() {
        let (store, tokenStore) = makeStores()
        tokenStore.saveAgentUrl("https://agent.example.com")
        tokenStore.saveTokens(accessToken: "at", refreshToken: "rt")

        let profile = store.captureActiveProfile(tokenStore: tokenStore, label: "Direct: agent")

        XCTAssertEqual(profile.mode, "direct")
        XCTAssertEqual(profile.agentUrl, "https://agent.example.com")
        XCTAssertEqual(profile.accessToken, "at")
        XCTAssertEqual(store.getActiveProfileId(), profile.id)
        XCTAssertEqual(store.listProfiles().count, 1)
    }

    func testActivateProfileLoadsDirectCredentials() {
        let (store, tokenStore) = makeStores()
        tokenStore.saveRelayCredentials(
            serverUrl: "https://old-relay.example.com", agentId: "OLD", pairingId: "P",
            deviceId: "D", accessToken: "old", refreshToken: "old"
        )
        store.saveProfile(ConnectionStore.ConnectionProfile(
            id: "p1", label: "Direct", mode: "direct", agentUrl: "https://a.example.com",
            accessToken: "at", refreshToken: "rt", createdAt: 1
        ))

        store.activateProfile(id: "p1", tokenStore: tokenStore)

        XCTAssertEqual(tokenStore.getConnectionMode(), .direct, "old relay keys are cleared first")
        XCTAssertEqual(tokenStore.getAgentUrl(), "https://a.example.com")
        XCTAssertEqual(tokenStore.getAccessToken(), "at")
        XCTAssertEqual(store.getActiveProfileId(), "p1")
    }

    func testActivateIncompleteProfileClearsTokensButStaysInactive() {
        let (store, tokenStore) = makeStores()
        tokenStore.saveAgentUrl("https://a.example.com")
        tokenStore.saveTokens(accessToken: "at", refreshToken: "rt")
        store.saveProfile(ConnectionStore.ConnectionProfile(
            id: "broken", label: "Relay", mode: "relay", relayServerUrl: "https://r.example.com",
            createdAt: 1
        ))

        store.activateProfile(id: "broken", tokenStore: tokenStore)

        XCTAssertNil(tokenStore.getAccessToken(), "TokenStore was cleared")
        XCTAssertNil(store.getActiveProfileId(), "incomplete profile is never marked active")
    }

    func testActivateUnknownProfileIsNoOp() {
        let (store, tokenStore) = makeStores()
        tokenStore.saveTokens(accessToken: "at", refreshToken: "rt")
        store.activateProfile(id: "missing", tokenStore: tokenStore)
        XCTAssertEqual(tokenStore.getAccessToken(), "at", "unknown id must not clear anything")
    }
}
