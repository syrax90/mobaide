import XCTest
@testable import Mobaide

@MainActor
final class AttentionCoordinatorTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!
    private var seen: AttentionSeenStore!

    override func setUp() {
        super.setUp()
        suiteName = "attention-coordinator-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        seen = AttentionSeenStore(
            preferences: PreferencesStore(defaults: defaults),
            agentKey: { "agent-1" }
        )
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private func makeCoordinator(
        presenter: FakeAttentionPresenter,
        events: AsyncStream<AgentEvent> = AsyncStream { $0.finish() },
        authStates: AsyncStream<AuthState> = AsyncStream { $0.finish() },
        snapshot: @escaping @Sendable () async throws -> [SessionResponse] = { [] }
    ) -> AttentionCoordinator {
        AttentionCoordinator(
            eventStream: { events },
            authStates: { authStates },
            presenter: presenter,
            snapshot: snapshot,
            seen: seen
        )
    }

    /// Put the agent past its first reconcile, so posting is driven by the
    /// seen-set rather than suppressed by the baseline branch (covered on its
    /// own in `AttentionCoordinatorSeenTests`).
    private func markBaselineTaken() {
        seen.adoptBaseline([])
    }

    func testSessionAttentionPostsWithSessionId() async {
        let presenter = FakeAttentionPresenter()
        let (events, continuation) = AsyncStream.makeStream(of: AgentEvent.self)
        let coordinator = makeCoordinator(presenter: presenter, events: events)
        coordinator.start()
        continuation.yield(.sessionAttention(makeSessionResponse(id: "s1", attention: "turn_finished")))
        continuation.finish()
        await waitUntil { presenter.postedIds == ["s1"] }
        XCTAssertEqual(presenter.postedIds, ["s1"])
        XCTAssertTrue(presenter.removed.isEmpty)
    }

    func testSessionUpdatedClearedRemoves() async {
        let presenter = FakeAttentionPresenter()
        let (events, continuation) = AsyncStream.makeStream(of: AgentEvent.self)
        let coordinator = makeCoordinator(presenter: presenter, events: events)
        coordinator.start()
        continuation.yield(.sessionUpdated(makeSessionResponse(id: "s1", attention: nil)))
        continuation.finish()
        await waitUntil { presenter.removed == ["s1"] }
        XCTAssertEqual(presenter.removed, ["s1"])
        XCTAssertTrue(presenter.posted.isEmpty)
    }

    func testReconcilePostsMissingAndRemovesStale() async {
        // A (snapshot attention set) = {a, c}; B (delivered) = {c, d}.
        markBaselineTaken()
        let presenter = FakeAttentionPresenter(delivered: ["c", "d"])
        let coordinator = makeCoordinator(
            presenter: presenter,
            snapshot: {
                [
                    makeSessionResponse(id: "a", attention: "awaiting_permission"),
                    makeSessionResponse(id: "c", attention: "ended"),
                    makeSessionResponse(id: "e", attention: nil),
                ]
            }
        )
        await coordinator.reconcile()
        XCTAssertEqual(presenter.postedIds, ["a"], "post A\\B")
        XCTAssertEqual(presenter.removed, ["d"], "remove B\\A")
    }

    func testAuthorizationRequestedOnceOnFirstFullyConnected() async {
        let presenter = FakeAttentionPresenter()
        let (authStates, continuation) = AsyncStream.makeStream(of: AuthState.self)
        let coordinator = makeCoordinator(presenter: presenter, authStates: authStates)
        coordinator.start()
        continuation.yield(.userOnly)
        continuation.yield(.fullyConnected)
        continuation.yield(.userOnly)
        continuation.yield(.fullyConnected)
        continuation.finish()
        await waitUntil { presenter.authRequestCount >= 1 }
        // Allow any wrongly duplicated request to surface before asserting once.
        await waitUntil(timeout: .milliseconds(200)) { false }
        XCTAssertEqual(presenter.authRequestCount, 1)
    }

    func testAuthorizationGrantReconcilesToRecoverDroppedAlerts() async {
        // `post` drops alerts while authorization is undecided, so a grant is
        // what recovers them — and it arrives from whichever request won, not
        // only the one made on connect.
        let presenter = FakeAttentionPresenter()
        let snapshotCount = Counter()
        let (authStates, continuation) = AsyncStream.makeStream(of: AuthState.self)
        let coordinator = makeCoordinator(
            presenter: presenter,
            authStates: authStates,
            snapshot: { snapshotCount.increment(); return [] }
        )
        coordinator.start()
        continuation.yield(.fullyConnected)
        await waitUntil { snapshotCount.value == 1 }
        coordinator.authorizationGranted()
        await waitUntil { snapshotCount.value == 2 }
        XCTAssertEqual(snapshotCount.value, 2, "a grant reconciles to recover dropped alerts")
        continuation.finish()
    }

    func testAuthorizationGrantAfterTeardownDoesNotReconcile() async {
        // Answering the prompt after a logout / agent switch must not resurrect
        // the previous agent's alerts past `removeAll()`.
        let presenter = FakeAttentionPresenter()
        let snapshotCount = Counter()
        let (authStates, continuation) = AsyncStream.makeStream(of: AuthState.self)
        let coordinator = makeCoordinator(
            presenter: presenter,
            authStates: authStates,
            snapshot: { snapshotCount.increment(); return [] }
        )
        coordinator.start()
        continuation.yield(.fullyConnected)
        await waitUntil { snapshotCount.value == 1 }
        continuation.yield(.userOnly)
        await waitUntil { presenter.removeAllCount == 1 }
        coordinator.authorizationGranted()
        await waitUntil(timeout: .milliseconds(200)) { false }
        XCTAssertEqual(snapshotCount.value, 1, "no reconcile once the agent connection is gone")
        continuation.finish()
    }

    func testReconcileSkipsWhenSnapshotFails() async {
        // A transient REST failure must not be read as an empty attention set;
        // otherwise reconcile would remove every delivered notification.
        struct SnapshotFailure: Error {}
        markBaselineTaken()
        let presenter = FakeAttentionPresenter(delivered: ["c", "d"])
        let coordinator = makeCoordinator(
            presenter: presenter,
            snapshot: { throw SnapshotFailure() }
        )
        await coordinator.reconcile()
        XCTAssertTrue(presenter.posted.isEmpty, "no posts on a failed snapshot")
        XCTAssertTrue(presenter.removed.isEmpty, "delivered notifications survive a failed snapshot")
    }

    func testSessionsChangedNeverPostsOrRemoves() async {
        // Android parity: a sessions.changed list refresh never raises an
        // alert, even when its sessions carry an attention reason.
        let presenter = FakeAttentionPresenter()
        let (events, continuation) = AsyncStream.makeStream(of: AgentEvent.self)
        let coordinator = makeCoordinator(presenter: presenter, events: events)
        coordinator.start()
        continuation.yield(.sessionsChanged([makeSessionResponse(id: "listed", attention: "awaiting_permission")]))
        continuation.yield(.sessionAttention(makeSessionResponse(id: "posted", attention: "turn_finished")))
        continuation.finish()
        await waitUntil { presenter.postedIds == ["posted"] }
        XCTAssertEqual(presenter.postedIds, ["posted"])
        XCTAssertTrue(presenter.removed.isEmpty)
    }

    func testUnauthenticatedClearsAllDeliveredNotifications() async {
        // Logout drops every alert so the next account never sees the previous
        // user's session titles (cross-account isolation).
        let presenter = FakeAttentionPresenter()
        let (authStates, continuation) = AsyncStream.makeStream(of: AuthState.self)
        let coordinator = makeCoordinator(presenter: presenter, authStates: authStates)
        coordinator.start()
        continuation.yield(.unauthenticated)
        continuation.finish()
        await waitUntil { presenter.removeAllCount == 1 }
        XCTAssertEqual(presenter.removeAllCount, 1)
    }

    func testAgentDisconnectClearsDeliveredNotifications() async {
        // Agent-scoped teardown: dropping from .fullyConnected to .userOnly
        // (agent disconnect/switch) clears the previous agent's alerts so they
        // can't linger or deep-link into a session the next agent can't resolve.
        let presenter = FakeAttentionPresenter()
        let (authStates, continuation) = AsyncStream.makeStream(of: AuthState.self)
        let coordinator = makeCoordinator(presenter: presenter, authStates: authStates)
        coordinator.start()
        continuation.yield(.fullyConnected)
        continuation.yield(.userOnly)
        continuation.finish()
        await waitUntil { presenter.removeAllCount == 1 }
        XCTAssertEqual(presenter.removeAllCount, 1)
    }

    func testReconcilesOnFullyConnected() async {
        // Cold launch / agent (re)connect: reaching .fullyConnected reconciles
        // so attention missed while the app wasn't running is posted (the
        // connect snapshot arrives as sessions.changed, which is ignored).
        markBaselineTaken()
        let presenter = FakeAttentionPresenter()
        let (authStates, continuation) = AsyncStream.makeStream(of: AuthState.self)
        let coordinator = makeCoordinator(
            presenter: presenter,
            authStates: authStates,
            snapshot: { [makeSessionResponse(id: "x", attention: "awaiting_permission")] }
        )
        coordinator.start()
        continuation.yield(.fullyConnected)
        continuation.finish()
        await waitUntil { presenter.postedIds == ["x"] }
        XCTAssertEqual(presenter.postedIds, ["x"])
    }

    func testReconcileReadsDeliveredBeforeSnapshot() async {
        // A live .sessionAttention posting during the snapshot await must not be
        // removed by the B\A loop: reconcile samples delivered before the
        // snapshot, so a notification posted mid-snapshot is not yet in B.
        markBaselineTaken()
        let presenter = FakeAttentionPresenter(delivered: [])
        let coordinator = makeCoordinator(
            presenter: presenter,
            snapshot: {
                presenter.setDelivered(["live"])
                return [makeSessionResponse(id: "a", attention: "awaiting_permission")]
            }
        )
        await coordinator.reconcile()
        XCTAssertFalse(presenter.removed.contains("live"), "a mid-snapshot live post must survive reconcile")
        XCTAssertEqual(presenter.postedIds, ["a"])
    }

    func testTeardownDuringReconcileIsNotBlockedAndDoesNotReLeak() async {
        // A logout/agent-switch arriving while reconcile's snapshot is in flight
        // must not block behind it (removeAll runs promptly), and the cancelled
        // reconcile must not re-post the previous agent's alerts.
        markBaselineTaken()
        let presenter = FakeAttentionPresenter()
        let (authStates, continuation) = AsyncStream.makeStream(of: AuthState.self)
        let gate = AsyncGate()
        let coordinator = makeCoordinator(
            presenter: presenter,
            authStates: authStates,
            snapshot: {
                await gate.wait()
                return [makeSessionResponse(id: "old-agent", attention: "awaiting_permission")]
            }
        )
        coordinator.start()
        continuation.yield(.fullyConnected)  // spawns reconcile; it blocks on the gate
        continuation.yield(.userOnly)         // teardown: cancels reconcile + removeAll
        continuation.finish()
        await waitUntil { presenter.removeAllCount == 1 }
        XCTAssertEqual(presenter.removeAllCount, 1, "teardown must not wait on the gated snapshot")
        gate.open()
        await waitUntil(timeout: .milliseconds(300)) { !presenter.postedIds.isEmpty }
        XCTAssertTrue(presenter.postedIds.isEmpty, "a cancelled reconcile must not re-post after teardown")
    }

    func testBufferedAttentionAfterTeardownIsNotPosted() async {
        // The event loop and auth loop are independent tasks: an attention event
        // processed after an agent-leave teardown must not re-post a stale alert.
        let presenter = FakeAttentionPresenter()
        let (events, eventContinuation) = AsyncStream.makeStream(of: AgentEvent.self)
        let (authStates, authContinuation) = AsyncStream.makeStream(of: AuthState.self)
        let coordinator = makeCoordinator(presenter: presenter, events: events, authStates: authStates)
        coordinator.start()
        authContinuation.yield(.fullyConnected)
        authContinuation.yield(.userOnly)  // agent leaves; latestAuthState = .userOnly
        authContinuation.finish()
        await waitUntil { presenter.removeAllCount == 1 }
        eventContinuation.yield(.sessionAttention(makeSessionResponse(id: "stale", attention: "turn_finished")))
        eventContinuation.finish()
        await waitUntil(timeout: .milliseconds(300)) { !presenter.postedIds.isEmpty }
        XCTAssertTrue(presenter.postedIds.isEmpty, "attention after agent-leave must not post")
    }
}
