import XCTest
@testable import Mobaide

/// Agent-scoped replay: a remote alert names the agent that raised it, and
/// replaying it against a different connected agent would open a session that
/// agent cannot resolve. Agent-less (locally posted) taps keep the old
/// behaviour and stay in `DeepLinkCoordinatorTests`.
@MainActor
final class DeepLinkCoordinatorAgentSwitchTests: XCTestCase {
    /// The connected agent id lives behind a lock because the coordinator reads
    /// it from a nonisolated `@Sendable` closure (the live one reads `TokenStore`).
    private final class AgentIdBox: @unchecked Sendable {
        private let lock = NSLock()
        private var value: String?
        var current: String? {
            get { lock.withLock { value } }
            set { lock.withLock { value = newValue } }
        }
    }

    /// Drives the three injected seams: the auth stream, the connected agent
    /// id, and the agent switch (recorded, optionally failing, optionally held
    /// in flight).
    @MainActor
    private final class Harness {
        let router: Router
        let coordinator: DeepLinkCoordinator

        private(set) var switchRequests: [String] = []
        /// Thrown by every switch while set.
        var switchFailure: (any Error)?

        var connectedAgentId: String? {
            get { agentIdBox.current }
            set { agentIdBox.current = newValue }
        }

        private let agentIdBox: AgentIdBox
        private let continuation: AsyncStream<AuthState>.Continuation
        private var gate: AsyncStream<Void>?
        private var gateContinuation: AsyncStream<Void>.Continuation?

        /// Boxes the not-yet-constructed `self` so the coordinator's
        /// `@Sendable` closures have a Sendable capture (mirrors
        /// `PushRegistrationCoordinatorTests`).
        private final class Box: @unchecked Sendable { var harness: Harness? }

        init(connectedAgentId: String?, initialAuthState: AuthState = .fullyConnected) {
            let router = Router()
            let agentIdBox = AgentIdBox()
            agentIdBox.current = connectedAgentId
            let (states, continuation) = AsyncStream.makeStream(of: AuthState.self)
            let box = Box()
            self.router = router
            self.agentIdBox = agentIdBox
            self.continuation = continuation
            coordinator = DeepLinkCoordinator(
                router: router,
                authStates: { states },
                initialAuthState: initialAuthState,
                connectedAgentId: { agentIdBox.current },
                switchAgent: { agentId in
                    let outcome = await MainActor.run { box.harness?.beginSwitch(agentId) }
                    if let gate = outcome?.gate { for await _ in gate {} }
                    if let failure = outcome?.failure { throw failure }
                }
            )
            box.harness = self
        }

        func yield(_ state: AuthState) { continuation.yield(state) }

        /// Hold the next switch in flight — the window in which the real
        /// `connectAgentViaRelay` cycles the auth state itself.
        func holdSwitch() {
            let (stream, continuation) = AsyncStream.makeStream(of: Void.self)
            gate = stream
            gateContinuation = continuation
        }

        func releaseSwitch() { gateContinuation?.finish() }

        private func beginSwitch(_ agentId: String) -> (gate: AsyncStream<Void>?, failure: (any Error)?) {
            switchRequests.append(agentId)
            // One-shot: a hold arms the *next* switch only, so a follow-up
            // switch runs to completion instead of re-consuming a spent stream.
            let held = gate
            gate = nil
            return (held, switchFailure)
        }
    }

    private struct SwitchFailure: Error, LocalizedError {
        var errorDescription: String? { "agent offline" }
    }

    func testMatchingAgentIdNavigatesWithoutSwitching() {
        let harness = Harness(connectedAgentId: "agent-1")
        harness.coordinator.handleTap(sessionId: "s1", agentId: "agent-1")

        XCTAssertEqual(harness.router.path, [.sessionDetail(sessionId: "s1")])
        XCTAssertNil(harness.coordinator.pendingSessionId)
        XCTAssertNil(harness.coordinator.pendingAgentId)
        XCTAssertEqual(harness.switchRequests, [], "already on the raising agent")
    }

    func testMismatchedAgentIdSwitchesOnceAndNavigatesAfterConnecting() async {
        let harness = Harness(connectedAgentId: "agent-1")
        harness.coordinator.start()
        harness.coordinator.handleTap(sessionId: "s1", agentId: "agent-2")

        await waitUntil { harness.switchRequests == ["agent-2"] }
        XCTAssertEqual(harness.switchRequests, ["agent-2"])
        XCTAssertEqual(harness.router.path, [], "no navigation until the raising agent is connected")
        XCTAssertEqual(harness.coordinator.pendingSessionId, "s1")
        XCTAssertEqual(harness.coordinator.pendingAgentId, "agent-2")

        // The real switch ends in `markAgentConnected()`; mirror both halves.
        harness.connectedAgentId = "agent-2"
        harness.yield(.fullyConnected)
        await waitUntil { harness.router.path == [.sessionDetail(sessionId: "s1")] }
        XCTAssertEqual(harness.router.path, [.sessionDetail(sessionId: "s1")])
        XCTAssertNil(harness.coordinator.pendingSessionId)
        XCTAssertEqual(harness.switchRequests, ["agent-2"], "the now-connected agent must not be re-switched")
    }

    func testAuthCycleDuringSwitchDoesNotStartASecondSwitch() async {
        let harness = Harness(connectedAgentId: "agent-1")
        harness.holdSwitch()
        harness.coordinator.start()
        harness.coordinator.handleTap(sessionId: "s1", agentId: "agent-2")
        await waitUntil { harness.switchRequests == ["agent-2"] }

        // `connectAgentViaRelay` drops and re-establishes the agent connection
        // itself, so the coordinator sees this cycle mid-switch.
        harness.yield(.userOnly)
        harness.yield(.fullyConnected)
        await waitUntil(timeout: .milliseconds(200)) { false }
        XCTAssertEqual(harness.switchRequests, ["agent-2"], "one switch per tap across the cycle it drives")
        XCTAssertEqual(harness.router.path, [])

        harness.connectedAgentId = "agent-2"
        harness.releaseSwitch()
        await waitUntil { harness.router.path == [.sessionDetail(sessionId: "s1")] }
        XCTAssertEqual(harness.router.path, [.sessionDetail(sessionId: "s1")])
        XCTAssertEqual(harness.switchRequests, ["agent-2"])
    }

    func testFailedSwitchClearsStashPublishesErrorAndDoesNotRetry() async {
        let harness = Harness(connectedAgentId: "agent-1")
        harness.switchFailure = SwitchFailure()
        harness.coordinator.start()
        harness.coordinator.handleTap(sessionId: "s1", agentId: "agent-2")

        await waitUntil { harness.coordinator.switchError != nil }
        XCTAssertEqual(
            harness.coordinator.switchError?.contains("agent offline"),
            true,
            "the toast must carry why the connect failed"
        )
        XCTAssertNil(harness.coordinator.pendingSessionId)
        XCTAssertNil(harness.coordinator.pendingAgentId)
        XCTAssertEqual(harness.router.path, [], "a failed switch leaves the user where they were")

        harness.yield(.fullyConnected)
        await waitUntil(timeout: .milliseconds(200)) { false }
        XCTAssertEqual(harness.switchRequests, ["agent-2"], "a cleared stash must not re-attempt the switch")
        XCTAssertEqual(harness.router.path, [])
    }

    func testFailedSwitchDoesNotClearATapThatSupersededIt() async {
        let harness = Harness(connectedAgentId: "agent-1")
        harness.switchFailure = SwitchFailure()
        harness.holdSwitch()
        harness.coordinator.start()
        harness.coordinator.handleTap(sessionId: "s1", agentId: "agent-2")
        await waitUntil { harness.switchRequests == ["agent-2"] }

        // A second alert is tapped while the first switch is still in flight.
        harness.coordinator.handleTap(sessionId: "s2", agentId: "agent-3")
        XCTAssertEqual(harness.coordinator.pendingSessionId, "s2")
        XCTAssertEqual(harness.coordinator.pendingAgentId, "agent-3")
        XCTAssertEqual(harness.switchRequests, ["agent-2"], "no second switch while one is mid-flight")

        // Fail the held switch; the replacement must connect on its own terms.
        // Clearing the failure here is race-free: nothing else runs on the main
        // actor until this test suspends.
        harness.releaseSwitch()
        harness.switchFailure = nil

        await waitUntil { harness.switchRequests == ["agent-2", "agent-3"] }
        XCTAssertEqual(harness.switchRequests, ["agent-2", "agent-3"])
        XCTAssertEqual(
            harness.coordinator.pendingSessionId,
            "s2",
            "the superseded switch's verdict must not swallow the newer tap"
        )
        XCTAssertEqual(harness.coordinator.pendingAgentId, "agent-3")
        XCTAssertNil(harness.coordinator.switchError, "the older target's failure is not this tap's failure")
        XCTAssertEqual(harness.router.path, [])

        harness.connectedAgentId = "agent-3"
        harness.yield(.fullyConnected)
        await waitUntil { harness.router.path == [.sessionDetail(sessionId: "s2")] }
        XCTAssertEqual(harness.router.path, [.sessionDetail(sessionId: "s2")], "the surviving tap still lands")
    }

    func testLogoutDuringSwitchClearsTheInFlightFlag() async {
        let harness = Harness(connectedAgentId: "agent-1")
        harness.holdSwitch()
        harness.coordinator.start()
        harness.coordinator.handleTap(sessionId: "s1", agentId: "agent-2")
        await waitUntil { harness.switchRequests == ["agent-2"] }
        XCTAssertTrue(harness.coordinator.isSwitchingAgent)

        harness.yield(.unauthenticated)
        await waitUntil { harness.coordinator.isSwitchingAgent == false }
        XCTAssertFalse(harness.coordinator.isSwitchingAgent, "a spinner must not survive the session that raised it")
        XCTAssertNil(harness.coordinator.pendingSessionId)

        // The cancelled switch unwinds afterwards and must resurrect nothing.
        harness.releaseSwitch()
        await waitUntil(timeout: .milliseconds(200)) { false }
        XCTAssertFalse(harness.coordinator.isSwitchingAgent)
        XCTAssertEqual(harness.router.path, [])
    }

    // MARK: - Sessions drawer: another agent's sessions list

    func testOpenSessionsOfTheConnectedAgentPopsToTheListWithoutSwitching() {
        let harness = Harness(connectedAgentId: "agent-1")
        harness.router.navigate(to: .sessionList)
        harness.router.navigate(to: .sessionDetail(sessionId: "s1"))

        harness.coordinator.openSessions(ofAgent: "agent-1")

        XCTAssertEqual(harness.router.path, [.sessionList], "the list beneath the terminal, not a second one on top")
        XCTAssertEqual(harness.switchRequests, [], "already on that agent")
        XCTAssertNil(harness.coordinator.pendingRoute)
        XCTAssertNil(harness.coordinator.pendingAgentId)
    }

    func testOpenSessionsOfAnotherAgentSwitchesThenLandsOnItsList() async {
        let harness = Harness(connectedAgentId: "agent-1")
        harness.router.navigate(to: .sessionList)
        harness.router.navigate(to: .sessionDetail(sessionId: "s1"))
        harness.holdSwitch()
        harness.coordinator.start()

        harness.coordinator.openSessions(ofAgent: "agent-2")

        await waitUntil { harness.switchRequests == ["agent-2"] }
        XCTAssertEqual(harness.switchRequests, ["agent-2"])
        XCTAssertEqual(
            harness.router.path, [.sessionList, .sessionDetail(sessionId: "s1")],
            "no navigation until the wanted agent is connected"
        )
        XCTAssertEqual(harness.coordinator.pendingRoute, .sessionList)
        XCTAssertEqual(harness.coordinator.pendingAgentId, "agent-2")
        XCTAssertNil(harness.coordinator.pendingSessionId, "a pending list is not a pending notification tap")
        XCTAssertTrue(harness.coordinator.isSwitchingAgent, "the drawer's switch shows the same connecting toast")

        // The real switch ends in `markAgentConnected()`; mirror both halves.
        harness.connectedAgentId = "agent-2"
        harness.releaseSwitch()
        harness.yield(.fullyConnected)
        await waitUntil { harness.router.path == [.sessionList] }
        XCTAssertEqual(harness.router.path, [.sessionList])
        XCTAssertNil(harness.coordinator.pendingRoute)
        XCTAssertFalse(harness.coordinator.isSwitchingAgent)
        XCTAssertEqual(harness.switchRequests, ["agent-2"], "the now-connected agent must not be re-switched")
    }

    func testANotificationTapSupersedesAPendingListAndViceVersa() {
        let harness = Harness(connectedAgentId: "agent-1", initialAuthState: .userOnly)

        harness.coordinator.openSessions(ofAgent: "agent-2")
        XCTAssertEqual(harness.coordinator.pendingRoute, .sessionList)

        harness.coordinator.handleTap(sessionId: "s1", agentId: "agent-3")
        XCTAssertEqual(harness.coordinator.pendingRoute, .sessionDetail(sessionId: "s1"))
        XCTAssertEqual(harness.coordinator.pendingSessionId, "s1")
        XCTAssertEqual(harness.coordinator.pendingAgentId, "agent-3")

        harness.coordinator.openSessions(ofAgent: "agent-2")
        XCTAssertEqual(harness.coordinator.pendingRoute, .sessionList, "one stash: the newest request wins")
        XCTAssertNil(harness.coordinator.pendingSessionId)
        XCTAssertEqual(harness.coordinator.pendingAgentId, "agent-2")
    }
}
