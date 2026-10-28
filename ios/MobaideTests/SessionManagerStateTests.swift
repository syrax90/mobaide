import XCTest
@testable import Mobaide

final class SessionManagerStateTests: XCTestCase {
    func testInitialStateMatrix() async {
        let none = SessionHarness()
        let userOnly = SessionHarness(userLoggedIn: true)
        let full = SessionHarness(userLoggedIn: true, agentConnected: true)
        let agentOnly = SessionHarness(agentConnected: true)

        let noneState = await none.sessionManager.currentAuthState
        let userState = await userOnly.sessionManager.currentAuthState
        let fullState = await full.sessionManager.currentAuthState
        let agentOnlyState = await agentOnly.sessionManager.currentAuthState

        XCTAssertEqual(noneState, .unauthenticated)
        XCTAssertEqual(userState, .userOnly)
        XCTAssertEqual(fullState, .fullyConnected)
        XCTAssertEqual(agentOnlyState, .unauthenticated)
    }

    func testMarkUserLoggedInAndAgentConnected() async {
        let harness = SessionHarness()
        await harness.sessionManager.markUserLoggedIn(userId: "u1") {}
        var state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly)

        await harness.sessionManager.markAgentConnected()
        state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .fullyConnected)
        let loggedIn = await harness.sessionManager.isLoggedIn()
        let userAuthenticated = await harness.sessionManager.isUserAuthenticated()
        XCTAssertTrue(loggedIn)
        XCTAssertTrue(userAuthenticated)
    }

    func testMarkIncompatibleServerPinsState() async {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let info = AuthState.IncompatibleServer(
            reason: "protocol too old",
            minClientVersion: 7,
            storeUrl: "https://apps.apple.com/app",
            storeFallbackUrl: "https://example.com"
        )
        await harness.sessionManager.markIncompatibleServer(info)
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .incompatibleServer(info))
    }

    func testAgentDisconnectDemotesOnlyFromFullyConnected() async {
        let full = SessionHarness(userLoggedIn: true, agentConnected: true)
        await full.sessionManager.agentDisconnect()
        let demoted = await full.sessionManager.currentAuthState
        XCTAssertEqual(demoted, .userOnly)
        XCTAssertNil(full.tokenStore.getAccessToken())

        let userOnly = SessionHarness(userLoggedIn: true)
        userOnly.tokenStore.saveTokens(accessToken: "a", refreshToken: "r")
        await userOnly.sessionManager.markUserLoggedIn(userId: "u1") {}
        await userOnly.sessionManager.agentDisconnect()
        let unchanged = await userOnly.sessionManager.currentAuthState
        XCTAssertEqual(unchanged, .userOnly)
        XCTAssertNil(userOnly.tokenStore.getAccessToken(), "agentDisconnect always clears agent tokens")
    }

    func testInvalidateUserSessionClearsBothStoresAndEmits() async throws {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let events = StreamCollector(harness.sessionManager.sessionEvents())

        await harness.sessionManager.invalidateUserSession(.tokenRejected)

        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .unauthenticated)
        XCTAssertNil(harness.tokenStore.getAccessToken())
        XCTAssertFalse(harness.userIdentityStore.isUserLoggedIn())
        let event = try await events.next()
        XCTAssertEqual(event, .tokenRejected)
    }

    func testManualLogoutPurgesEveryAgentRecentCustomPathBucket() async {
        // The paths name the user's own workstation, so a second account on
        // this device must not be offered the first account's directories.
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        harness.preferencesStore.addRecentCustomPath(agentKey: "a1", path: "/srv/app")
        harness.preferencesStore.addRecentCustomPath(agentKey: "a2", path: "/home/alice/work")

        await harness.sessionManager.userLogout()

        XCTAssertEqual(harness.preferencesStore.getRecentCustomPaths(agentKey: "a1"), [])
        XCTAssertEqual(
            harness.preferencesStore.getRecentCustomPaths(agentKey: "a2"), [],
            "a bucket for an agent other than the current one goes too"
        )
    }

    func testAccountRemovedPurgesRecentCustomPaths() async {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        harness.preferencesStore.addRecentCustomPath(agentKey: "a1", path: "/srv/app")

        await harness.sessionManager.invalidateUserSession(.accountRemoved)

        XCTAssertEqual(harness.preferencesStore.getRecentCustomPaths(agentKey: "a1"), [])
    }

    func testAgentDisconnectKeepsRecentCustomPaths() async {
        // An agent switch is not an identity change: the recents survive it
        // even though agentDisconnect clears the agent tokens next to them.
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        harness.preferencesStore.addRecentCustomPath(agentKey: "a1", path: "/srv/app")

        await harness.sessionManager.agentDisconnect()

        XCTAssertEqual(harness.preferencesStore.getRecentCustomPaths(agentKey: "a1"), ["/srv/app"])
        XCTAssertNil(harness.tokenStore.getAccessToken())
    }

    func testInvalidateUserSessionIsIdempotent() async throws {
        let harness = SessionHarness(userLoggedIn: true)
        await harness.sessionManager.invalidateUserSession(.tokenRejected)
        // Second invalidation while already unauthenticated must not re-emit.
        await harness.sessionManager.invalidateUserSession(.accountRemoved)

        let events = StreamCollector(harness.sessionManager.sessionEvents())
        let event = try await events.next()
        XCTAssertEqual(event, .tokenRejected)
        let silent = await events.expectNoElement()
        XCTAssertTrue(silent)
    }

    func testManualLogoutIsSilent() async {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let events = StreamCollector(harness.sessionManager.sessionEvents())

        await harness.sessionManager.userLogout()

        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .unauthenticated)
        XCTAssertNil(harness.tokenStore.getAccessToken())
        XCTAssertFalse(harness.userIdentityStore.isUserLoggedIn())
        let silent = await events.expectNoElement()
        XCTAssertTrue(silent)
    }

    func testSessionEventsReplayReachesLateSubscriber() async throws {
        let harness = SessionHarness(userLoggedIn: true)
        // Cold-start race: the invalidation fires before any subscriber exists.
        await harness.sessionManager.invalidateUserSession(.accountRemoved)

        let late = StreamCollector(harness.sessionManager.sessionEvents())
        let event = try await late.next()
        XCTAssertEqual(event, .accountRemoved)
    }

    func testMarkUserLoggedInClearsSessionEventReplay() async {
        let harness = SessionHarness(userLoggedIn: true)
        await harness.sessionManager.invalidateUserSession(.tokenRejected)
        await harness.sessionManager.markUserLoggedIn(userId: "u1") {}

        let subscriber = StreamCollector(harness.sessionManager.sessionEvents())
        let silent = await subscriber.expectNoElement()
        XCTAssertTrue(silent, "stale TokenRejected must not re-fire after re-login")
    }

    // MARK: - Account isolation (saved profiles + account switch)

    private static func seedRelayProfile(_ connectionStore: ConnectionStore) {
        connectionStore.saveProfile(ConnectionStore.ConnectionProfile(
            id: "p-relay",
            label: "Relay agent",
            mode: "relay",
            relayServerUrl: "https://relay.example.com",
            agentId: "agent-9",
            pairingId: "pair-9",
            deviceId: "device-9",
            accessToken: "relay-access",
            refreshToken: "relay-refresh"
        ))
        connectionStore.setActiveProfileId("p-relay")
    }

    private static func saveSecondAccount(_ identity: UserIdentityStore) {
        identity.saveUserSession(
            accessToken: "user-token-b",
            email: "b@example.com",
            userId: "u2",
            role: "user"
        )
    }

    func testInvalidateUserSessionPurgesSavedProfilesForEveryReason() async {
        // Profiles carry non-expiring relay tokens: one left behind lets the
        // next person on this device open a shell on the previous account's agent.
        for reason in [InvalidationReason.manual, .tokenRejected, .accountRemoved] {
            let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
            Self.seedRelayProfile(harness.connectionStore)

            await harness.sessionManager.invalidateUserSession(reason)

            XCTAssertTrue(harness.connectionStore.listProfiles().isEmpty, "\(reason)")
            XCTAssertNil(harness.connectionStore.getActiveProfileId(), "\(reason)")
        }
    }

    func testAccountSwitchFromUserOnlyTearsDownPreviousAccount() async throws {
        let harness = SessionHarness(userLoggedIn: true)
        harness.tokenStore.saveTokens(accessToken: "a", refreshToken: "r")
        try await assertAccountSwitchTearsDown(harness)
    }

    func testAccountSwitchFromFullyConnectedTearsDownPreviousAccount() async throws {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        try await assertAccountSwitchTearsDown(harness)
        XCTAssertNil(harness.tokenStore.getAgentUrl())
    }

    private func assertAccountSwitchTearsDown(_ harness: SessionHarness) async throws {
        Self.seedRelayProfile(harness.connectionStore)
        harness.preferencesStore.addRecentCustomPath(agentKey: "a1", path: "/home/alice/work")
        let changes = StreamCollector(harness.sessionManager.authStateChanges())
        let identity = harness.userIdentityStore

        await harness.sessionManager.markUserLoggedIn(userId: "u2") {
            SessionManagerStateTests.saveSecondAccount(identity)
        }

        // The `.unauthenticated` detour is what makes every sign-out observer
        // (user socket, caches, push registration) drop the previous account.
        let first = try await changes.next()
        XCTAssertEqual(first, .unauthenticated)
        let second = try await changes.next()
        XCTAssertEqual(second, .userOnly)

        XCTAssertEqual(identity.getUserId(), "u2", "persist runs after the teardown, so the new identity survives it")
        XCTAssertEqual(identity.getUserAccessToken(), "user-token-b")
        XCTAssertNil(harness.tokenStore.getAccessToken())
        XCTAssertNil(harness.tokenStore.getRefreshToken())
        XCTAssertTrue(harness.connectionStore.listProfiles().isEmpty)
        XCTAssertNil(harness.connectionStore.getActiveProfileId())
        XCTAssertEqual(harness.preferencesStore.getRecentCustomPaths(agentKey: "a1"), [])
    }

    func testAccountSwitchWhileSignedOutPurgesLeftoverProfiles() async {
        // Sign-outs in older builds never purged the store, so a fresh
        // sign-in as anyone must not inherit what they left behind.
        let harness = SessionHarness()
        Self.seedRelayProfile(harness.connectionStore)
        let identity = harness.userIdentityStore

        await harness.sessionManager.markUserLoggedIn(userId: "u2") {
            SessionManagerStateTests.saveSecondAccount(identity)
        }

        XCTAssertTrue(harness.connectionStore.listProfiles().isEmpty)
        XCTAssertEqual(identity.getUserId(), "u2")
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly)
    }

    func testAccountSwitchWhileSignedOutPurgesLeftoverAgentTokens() async {
        // Agent tokens without a user session start `.unauthenticated`, where
        // the sign-out teardown is a no-op; the switch must still wipe them.
        let harness = SessionHarness(agentConnected: true)
        let initial = await harness.sessionManager.currentAuthState
        XCTAssertEqual(initial, .unauthenticated)
        let identity = harness.userIdentityStore

        await harness.sessionManager.markUserLoggedIn(userId: "u2") {
            SessionManagerStateTests.saveSecondAccount(identity)
        }

        XCTAssertNil(harness.tokenStore.getAccessToken())
        XCTAssertNil(harness.tokenStore.getRefreshToken())
        XCTAssertNil(harness.tokenStore.getAgentUrl())
        XCTAssertEqual(identity.getUserId(), "u2")
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly)
    }

    func testTryMarkAgentConnectedRefusesWhenAnotherAccountSignedIn() async {
        let harness = SessionHarness(userLoggedIn: true)
        Self.saveSecondAccount(harness.userIdentityStore)
        let tokenStore = harness.tokenStore

        let connected = await harness.sessionManager.tryMarkAgentConnected(expectedUserId: "u1") {
            tokenStore.saveTokens(accessToken: "minted-for-u1", refreshToken: "r")
        }

        XCTAssertFalse(connected)
        XCTAssertNil(tokenStore.getAccessToken(), "persist must not run for another account")
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly)
    }

    func testTryMarkAgentConnectedPersistsForTheExpectedAccount() async {
        let harness = SessionHarness(userLoggedIn: true)
        let tokenStore = harness.tokenStore

        let connected = await harness.sessionManager.tryMarkAgentConnected(expectedUserId: "u1") {
            tokenStore.saveTokens(accessToken: "minted-for-u1", refreshToken: "r")
        }

        XCTAssertTrue(connected)
        XCTAssertEqual(tokenStore.getAccessToken(), "minted-for-u1")
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .fullyConnected)
    }

    func testSameUserReLoginKeepsAgentTokensProfilesAndRecents() async {
        let harness = SessionHarness(userLoggedIn: true)
        harness.tokenStore.saveTokens(accessToken: "a", refreshToken: "r")
        Self.seedRelayProfile(harness.connectionStore)
        harness.preferencesStore.addRecentCustomPath(agentKey: "a1", path: "/srv/app")
        let changes = StreamCollector(harness.sessionManager.authStateChanges())
        let identity = harness.userIdentityStore

        await harness.sessionManager.markUserLoggedIn(userId: "u1") {
            identity.saveUserSession(
                accessToken: "user-token-rotated",
                email: "user@example.com",
                userId: "u1",
                role: "user"
            )
        }

        let silent = await changes.expectNoElement()
        XCTAssertTrue(silent, "a same-account re-login must not detour through .unauthenticated")
        XCTAssertEqual(identity.getUserAccessToken(), "user-token-rotated")
        XCTAssertEqual(harness.tokenStore.getAccessToken(), "a")
        XCTAssertEqual(harness.connectionStore.listProfiles().map(\.id), ["p-relay"])
        XCTAssertEqual(harness.preferencesStore.getRecentCustomPaths(agentKey: "a1"), ["/srv/app"])
    }

    func testAuthStatesStreamReplaysCurrentAndFollowsTransitions() async throws {
        let harness = SessionHarness()
        let states = StreamCollector(harness.sessionManager.authStates())
        let initialState = try await states.next()
        XCTAssertEqual(initialState, .unauthenticated)

        await harness.sessionManager.markUserLoggedIn(userId: "u1") {}
        let userOnlyState = try await states.next()
        XCTAssertEqual(userOnlyState, .userOnly)

        await harness.sessionManager.markAgentConnected()
        let fullyConnectedState = try await states.next()
        XCTAssertEqual(fullyConnectedState, .fullyConnected)
    }

    func testAuthStateChangesStreamSkipsReplayedValue() async throws {
        let harness = SessionHarness(userLoggedIn: true)
        let changes = StreamCollector(harness.sessionManager.authStateChanges())
        let silent = await changes.expectNoElement()
        XCTAssertTrue(silent, "changes-only stream must not deliver the replayed initial value")

        await harness.sessionManager.markAgentConnected()
        let changedState = try await changes.next()
        XCTAssertEqual(changedState, .fullyConnected)
    }
}
