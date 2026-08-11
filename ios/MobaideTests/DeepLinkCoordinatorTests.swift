import XCTest
@testable import Mobaide

@MainActor
final class DeepLinkCoordinatorTests: XCTestCase {
    /// Every tap here is agent-less (a locally posted alert), so the two
    /// agent-scoped seams must stay untouched; `DeepLinkCoordinatorAgentSwitchTests`
    /// covers the branch that uses them.
    private func makeCoordinator(
        router: Router,
        authStates: @escaping @Sendable () -> AsyncStream<AuthState>,
        initialAuthState: AuthState
    ) -> DeepLinkCoordinator {
        DeepLinkCoordinator(
            router: router,
            authStates: authStates,
            initialAuthState: initialAuthState,
            connectedAgentId: { nil },
            switchAgent: { _ in XCTFail("an agent-less tap must not switch agents") }
        )
    }

    func testWarmTapNavigatesImmediately() {
        let router = Router()
        let coordinator = makeCoordinator(
            router: router,
            authStates: { AsyncStream { $0.finish() } },
            initialAuthState: .fullyConnected
        )
        coordinator.handleTap(sessionId: "s1", agentId: nil)
        XCTAssertEqual(router.path, [.sessionDetail(sessionId: "s1")])
        XCTAssertNil(coordinator.pendingSessionId)
    }

    func testWarmTapForAlreadyVisibleSessionDoesNotDuplicate() {
        let router = Router()
        let coordinator = makeCoordinator(
            router: router,
            authStates: { AsyncStream { $0.finish() } },
            initialAuthState: .fullyConnected
        )
        coordinator.handleTap(sessionId: "s1", agentId: nil)
        coordinator.handleTap(sessionId: "s1", agentId: nil)
        XCTAssertEqual(
            router.path,
            [.sessionDetail(sessionId: "s1")],
            "re-tapping the session already on top must not push a duplicate route"
        )
        XCTAssertNil(coordinator.pendingSessionId)
    }

    func testBlankSessionIdIgnored() {
        let router = Router()
        let coordinator = makeCoordinator(
            router: router,
            authStates: { AsyncStream { $0.finish() } },
            initialAuthState: .fullyConnected
        )
        coordinator.handleTap(sessionId: "   ", agentId: nil)
        XCTAssertEqual(router.path, [])
        XCTAssertNil(coordinator.pendingSessionId)
    }

    func testColdTapWaitsForFullyConnectedThenNavigates() async {
        let router = Router()
        let harness = SessionHarness(userLoggedIn: true, agentConnected: false)
        let sessionManager = harness.sessionManager
        let coordinator = makeCoordinator(
            router: router,
            authStates: { sessionManager.authStates() },
            initialAuthState: .userOnly
        )
        coordinator.start()
        coordinator.handleTap(sessionId: "s1", agentId: nil)
        XCTAssertEqual(router.path, [], "no navigation until fully connected")
        XCTAssertEqual(coordinator.pendingSessionId, "s1")

        await sessionManager.markAgentConnected()
        await waitUntil { router.path == [.sessionDetail(sessionId: "s1")] }
        XCTAssertEqual(router.path, [.sessionDetail(sessionId: "s1")])
        XCTAssertNil(coordinator.pendingSessionId)
    }

    func testTapConsumedOnceNoDoubleNavigation() async {
        let router = Router()
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let sessionManager = harness.sessionManager
        let coordinator = makeCoordinator(
            router: router,
            authStates: { sessionManager.authStates() },
            initialAuthState: .fullyConnected
        )
        coordinator.start()
        coordinator.handleTap(sessionId: "s1", agentId: nil)
        XCTAssertEqual(router.path, [.sessionDetail(sessionId: "s1")])
        // A later auth re-emit must not re-navigate (the stash was consumed).
        await sessionManager.agentDisconnect()
        await sessionManager.markAgentConnected()
        await waitUntil(timeout: .milliseconds(200)) { false }
        XCTAssertEqual(router.path, [.sessionDetail(sessionId: "s1")])
    }

    func testLogoutClearsPendingStashAndBlocksReplay() async {
        let router = Router()
        let harness = SessionHarness(userLoggedIn: true, agentConnected: false)
        let sessionManager = harness.sessionManager
        let coordinator = makeCoordinator(
            router: router,
            authStates: { sessionManager.authStates() },
            initialAuthState: .userOnly
        )
        coordinator.start()
        coordinator.handleTap(sessionId: "s1", agentId: nil)
        XCTAssertEqual(coordinator.pendingSessionId, "s1")

        await sessionManager.userLogout()
        await waitUntil { coordinator.pendingSessionId == nil }
        XCTAssertNil(coordinator.pendingSessionId)

        // A later re-login must not resurrect the cleared deep link.
        await sessionManager.markUserLoggedIn()
        await sessionManager.markAgentConnected()
        await waitUntil(timeout: .milliseconds(200)) { false }
        XCTAssertEqual(router.path, [], "a cleared stash must not replay after re-login")
    }
}
