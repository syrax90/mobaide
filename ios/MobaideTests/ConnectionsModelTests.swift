import XCTest
@testable import Mobaide

@MainActor
final class ConnectionsModelTests: XCTestCase {
    private var harness: SessionHarness!
    private var connectionStore: ConnectionStore!

    override func setUp() {
        super.setUp()
        harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        connectionStore = ConnectionStore(keychain: InMemoryKeychainStore())
    }

    private func makeModel() -> ConnectionsModel {
        ConnectionsModel(
            connectionStore: connectionStore,
            tokenStore: harness.tokenStore,
            sessionManager: harness.sessionManager
        )
    }

    private func seedProfiles() -> (direct: ConnectionStore.ConnectionProfile, relay: ConnectionStore.ConnectionProfile) {
        let direct = ConnectionStore.ConnectionProfile(
            id: "p-direct",
            label: "Home agent",
            mode: "direct",
            agentUrl: "https://agent.example.com",
            accessToken: "direct-access",
            refreshToken: "direct-refresh"
        )
        let relay = ConnectionStore.ConnectionProfile(
            id: "p-relay",
            label: "Relay agent",
            mode: "relay",
            relayServerUrl: "https://relay.example.com",
            agentId: "agent-9",
            pairingId: "pair-9",
            deviceId: "device-9",
            accessToken: "relay-access",
            refreshToken: "relay-refresh"
        )
        connectionStore.saveProfile(direct)
        connectionStore.saveProfile(relay)
        connectionStore.setActiveProfileId(direct.id)
        return (direct, relay)
    }

    func testLoadReflectsStoreContents() {
        let (direct, relay) = seedProfiles()

        let model = makeModel()

        XCTAssertEqual(model.profiles.map(\.id), [direct.id, relay.id])
        XCTAssertEqual(model.activeProfileId, direct.id)
    }

    func testSwitchProfileSwapsCredentialsAndReconnects() async throws {
        let (_, relay) = seedProfiles()
        let model = makeModel()
        let states = StreamCollector(harness.sessionManager.authStateChanges())

        await model.switchProfile(id: relay.id)

        // Disconnect demotes to userOnly, reconnect promotes back.
        let first = try await states.next()
        XCTAssertEqual(first, .userOnly)
        let second = try await states.next()
        XCTAssertEqual(second, .fullyConnected)

        XCTAssertEqual(model.activeProfileId, relay.id)
        XCTAssertEqual(harness.tokenStore.getRelayServerUrl(), "https://relay.example.com")
        XCTAssertEqual(harness.tokenStore.getAgentId(), "agent-9")
        XCTAssertEqual(harness.tokenStore.getAccessToken(), "relay-access")
        XCTAssertEqual(harness.tokenStore.getConnectionMode(), .relay)
    }

    func testSwitchToIncompleteProfileStaysDisconnected() async {
        let (direct, _) = seedProfiles()
        let incomplete = ConnectionStore.ConnectionProfile(
            id: "p-broken",
            label: "Broken",
            mode: "relay",
            relayServerUrl: "https://relay.example.com"
        )
        connectionStore.saveProfile(incomplete)
        let model = makeModel()

        await model.switchProfile(id: incomplete.id)

        // activateProfile bails before marking active; the agent stays
        // disconnected instead of being "connected" with an empty TokenStore.
        XCTAssertEqual(model.activeProfileId, direct.id)
        XCTAssertNil(harness.tokenStore.getAccessToken())
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly)
    }

    func testRemoveProfileDropsItAndClearsActiveId() {
        let (direct, relay) = seedProfiles()
        let model = makeModel()

        model.removeProfile(id: relay.id)
        XCTAssertEqual(model.profiles.map(\.id), [direct.id])
        XCTAssertEqual(model.activeProfileId, direct.id)

        model.removeProfile(id: direct.id)
        XCTAssertTrue(model.profiles.isEmpty)
        XCTAssertNil(model.activeProfileId)
    }
}
