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
        await harness.sessionManager.markUserLoggedIn()
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
        await userOnly.sessionManager.markUserLoggedIn()
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
        await harness.sessionManager.markUserLoggedIn()

        let subscriber = StreamCollector(harness.sessionManager.sessionEvents())
        let silent = await subscriber.expectNoElement()
        XCTAssertTrue(silent, "stale TokenRejected must not re-fire after re-login")
    }

    func testAuthStatesStreamReplaysCurrentAndFollowsTransitions() async throws {
        let harness = SessionHarness()
        let states = StreamCollector(harness.sessionManager.authStates())
        let initialState = try await states.next()
        XCTAssertEqual(initialState, .unauthenticated)

        await harness.sessionManager.markUserLoggedIn()
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
