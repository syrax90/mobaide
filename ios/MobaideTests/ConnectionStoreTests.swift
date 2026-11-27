import XCTest
@testable import Mobaide

final class ConnectionStoreTests: XCTestCase {
    private func makeStores() -> (ConnectionStore, TokenStore) {
        (
            ConnectionStore(keychain: InMemoryKeychainStore()),
            TokenStore(keychain: InMemoryKeychainStore())
        )
    }

    private func relayProfile(id: String, label: String = "One", agentId: String = "A1") -> ConnectionStore.ConnectionProfile {
        ConnectionStore.ConnectionProfile(
            id: id, label: label, mode: "relay", relayServerUrl: "https://r.example.com",
            agentId: agentId, pairingId: "P1", deviceId: "D1",
            accessToken: "at", refreshToken: "rt", createdAt: 1
        )
    }

    func testUpsertAndRemoveProfiles() {
        let (store, _) = makeStores()
        XCTAssertEqual(store.listProfiles(), [])

        store.saveProfile(relayProfile(id: "p1"))
        store.setActiveProfileId("p1")
        XCTAssertEqual(store.listProfiles().count, 1)
        XCTAssertEqual(store.getActiveProfileId(), "p1")

        store.saveProfile(relayProfile(id: "p1", label: "Renamed"))
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
        let json = #"[{"id":"p1","label":"L","mode":"relay"}]"#
        let profiles = try JSONDecoder().decode([ConnectionStore.ConnectionProfile].self, from: Data(json.utf8))
        XCTAssertEqual(profiles.first?.id, "p1")
        XCTAssertNotNil(profiles.first?.createdAt)
    }

    func testListProfilesLoadsOnlyRelayProfilesFromAnEarlierVersion() {
        // The list an earlier app version stored: one profile of the removed
        // direct connection mode, and relay profiles that still carry the
        // removed `agentUrl` key.
        let keychain = InMemoryKeychainStore()
        keychain.set(
            Data(#"""
            [
              {"id":"direct-1","label":"Direct: https://a.example.com","mode":"direct",
               "agentUrl":"https://a.example.com","accessToken":"direct-at","refreshToken":"direct-rt","createdAt":1},
              {"id":"relay-1","label":"Agent: Box","mode":"relay","agentUrl":null,
               "relayServerUrl":"https://r.example.com","agentId":"A1","pairingId":"P1","deviceId":"D1",
               "accessToken":"relay-at","refreshToken":"relay-rt","createdAt":2}
            ]
            """#.utf8),
            forKey: ConnectionStore.keyProfiles
        )
        let store = ConnectionStore(keychain: keychain)
        let tokenStore = TokenStore(keychain: InMemoryKeychainStore())

        let profiles = store.listProfiles()

        XCTAssertEqual(profiles.map(\.id), ["relay-1"])
        XCTAssertEqual(profiles.first?.agentId, "A1")
        XCTAssertEqual(profiles.first?.accessToken, "relay-at")

        store.activateProfile(id: "direct-1", tokenStore: tokenStore)
        XCTAssertNil(tokenStore.getAccessToken(), "a profile that is not loaded cannot be activated")
        XCTAssertNil(store.getActiveProfileId())
    }

    private func keychainOfAnEarlierVersion(activeId: String) -> InMemoryKeychainStore {
        let keychain = InMemoryKeychainStore()
        keychain.set(
            Data(#"""
            [
              {"id":"direct-1","label":"Direct: https://a.example.com","mode":"direct",
               "agentUrl":"https://a.example.com","accessToken":"direct-at","refreshToken":"direct-rt","createdAt":1},
              {"id":"relay-1","label":"Agent: Box","mode":"relay","agentUrl":null,
               "relayServerUrl":"https://r.example.com","agentId":"A1","pairingId":"P1","deviceId":"D1",
               "accessToken":"relay-at","refreshToken":"relay-rt","createdAt":2}
            ]
            """#.utf8),
            forKey: ConnectionStore.keyProfiles
        )
        keychain.set(activeId, forKey: ConnectionStore.keyActiveId)
        return keychain
    }

    func testListProfilesRemovesNonRelayProfilesFromStorageAndClearsADroppedActiveId() {
        let keychain = keychainOfAnEarlierVersion(activeId: "direct-1")
        let store = ConnectionStore(keychain: keychain)

        XCTAssertEqual(store.listProfiles().map(\.id), ["relay-1"])

        let stored = keychain.string(forKey: ConnectionStore.keyProfiles) ?? ""
        XCTAssertFalse(stored.contains("direct-at"), "the tokens of a dropped profile leave the keychain: \(stored)")
        XCTAssertFalse(stored.contains("direct-rt"))
        XCTAssertFalse(stored.contains("a.example.com"))
        XCTAssertTrue(stored.contains("relay-at"), "the relay profile stays stored: \(stored)")
        XCTAssertNil(store.getActiveProfileId(), "the active profile was dropped, so no profile is active")
    }

    func testListProfilesKeepsAnActiveRelayProfileActive() {
        let store = ConnectionStore(keychain: keychainOfAnEarlierVersion(activeId: "relay-1"))

        XCTAssertEqual(store.listProfiles().map(\.id), ["relay-1"])

        XCTAssertEqual(store.getActiveProfileId(), "relay-1")
    }

    func testCaptureActiveProfileSnapshotsTokenStore() {
        let (store, tokenStore) = makeStores()
        tokenStore.saveRelayCredentials(
            serverUrl: "https://relay.example.com", agentId: "A1", pairingId: "P1",
            deviceId: "D1", accessToken: "at", refreshToken: "rt"
        )

        let profile = store.captureActiveProfile(tokenStore: tokenStore, label: "Agent: box")

        XCTAssertEqual(profile.mode, "relay")
        XCTAssertEqual(profile.relayServerUrl, "https://relay.example.com")
        XCTAssertEqual(profile.agentId, "A1")
        XCTAssertEqual(profile.accessToken, "at")
        XCTAssertEqual(store.getActiveProfileId(), profile.id)
        XCTAssertEqual(store.listProfiles().count, 1)
    }

    func testActivateProfileLoadsItsCredentials() {
        let (store, tokenStore) = makeStores()
        tokenStore.saveRelayCredentials(
            serverUrl: "https://old-relay.example.com", agentId: "OLD", pairingId: "P",
            deviceId: "D", accessToken: "old", refreshToken: "old"
        )
        store.saveProfile(relayProfile(id: "p1", agentId: "NEW"))

        store.activateProfile(id: "p1", tokenStore: tokenStore)

        XCTAssertEqual(tokenStore.getRelayServerUrl(), "https://r.example.com", "the old connection is cleared first")
        XCTAssertEqual(tokenStore.getAgentId(), "NEW")
        XCTAssertEqual(tokenStore.getAccessToken(), "at")
        XCTAssertTrue(tokenStore.isLoggedIn())
        XCTAssertEqual(store.getActiveProfileId(), "p1")
    }

    func testActivateIncompleteProfileClearsTokensButStaysInactive() {
        let (store, tokenStore) = makeStores()
        tokenStore.saveRelayCredentials(
            serverUrl: "https://old-relay.example.com", agentId: "OLD", pairingId: "P",
            deviceId: "D", accessToken: "at", refreshToken: "rt"
        )
        store.saveProfile(ConnectionStore.ConnectionProfile(
            id: "broken", label: "Relay", mode: "relay", relayServerUrl: "https://r.example.com",
            createdAt: 1
        ))

        store.activateProfile(id: "broken", tokenStore: tokenStore)

        XCTAssertNil(tokenStore.getAccessToken(), "TokenStore was cleared")
        XCTAssertNil(store.getActiveProfileId(), "incomplete profile is never marked active")
    }
}
