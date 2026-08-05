import XCTest
@testable import Mobaide

/// Foreground reconcile as an *edge* trigger over latched attention.
///
/// The agent clears `attention` only on a client PTY write or a
/// `UserPromptSubmit`, so a session nobody answers reports the same reason in
/// every snapshot. Notification Center cannot be the memory of what was shown
/// — tapping or swiping the alert empties it — which is what made the app
/// re-post the same stale alert on every open.
@MainActor
final class AttentionCoordinatorSeenTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!
    private var seen: AttentionSeenStore!

    override func setUp() {
        super.setUp()
        suiteName = "attention-seen-coordinator-\(UUID().uuidString)"
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

    /// Snapshot source a test can re-point between reconciles (the snapshot
    /// closure is `@Sendable`, so a captured `var` will not compile).
    private final class SnapshotSource: @unchecked Sendable {
        private let lock = NSLock()
        private var storage: [SessionResponse]

        init(_ sessions: [SessionResponse]) { storage = sessions }

        var current: [SessionResponse] { lock.withLock { storage } }
        func set(_ sessions: [SessionResponse]) { lock.withLock { storage = sessions } }
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

    // MARK: - Baseline

    func testFirstReconcileSeedsTheBaselineWithoutPosting() async {
        // A device with no record has missed nothing it can act on, and
        // everything latched is already visible in the session list.
        let presenter = FakeAttentionPresenter()
        let coordinator = makeCoordinator(
            presenter: presenter,
            snapshot: { [makeSessionResponse(id: "s1", attention: "turn_finished")] }
        )

        await coordinator.reconcile()
        XCTAssertTrue(presenter.posted.isEmpty, "the first reconcile replays no history")

        // Seeded, not merely skipped: the next reconcile is silent too.
        await coordinator.reconcile()
        XCTAssertTrue(presenter.posted.isEmpty)
    }

    func testBaselineReconcileStillRemovesStaleAlerts() async {
        // The removal loop sits outside both branches: an alert whose attention
        // is gone must clear even on the very first reconcile.
        let presenter = FakeAttentionPresenter(delivered: ["gone"])
        let coordinator = makeCoordinator(
            presenter: presenter,
            snapshot: { [makeSessionResponse(id: "s1", attention: "turn_finished")] }
        )

        await coordinator.reconcile()

        XCTAssertEqual(presenter.removed, ["gone"])
        XCTAssertTrue(presenter.posted.isEmpty)
    }

    // MARK: - Edge triggering

    func testRepeatedReconcilesOverTheSameLatchedAttentionPostOnce() async {
        // The regression: dismissing the alert empties Notification Center, so
        // a delivered-set-only memory re-posted it at every app open.
        seen.adoptBaseline([])
        let presenter = FakeAttentionPresenter()
        let coordinator = makeCoordinator(
            presenter: presenter,
            snapshot: { [makeSessionResponse(id: "s1", attention: "turn_finished")] }
        )

        await coordinator.reconcile()
        await coordinator.reconcile()

        XCTAssertEqual(presenter.postedIds, ["s1"])
    }

    func testAttentionClearedInTheSnapshotReArmsTheSession() async {
        // Attention cleared while this device was away leaves no event to
        // observe, so the snapshot is the only thing that can re-arm it.
        seen.adoptBaseline([])
        let presenter = FakeAttentionPresenter()
        let latched = [makeSessionResponse(id: "s1", attention: "turn_finished")]
        let source = SnapshotSource(latched)
        let coordinator = makeCoordinator(
            presenter: presenter,
            snapshot: { source.current }
        )

        await coordinator.reconcile()
        source.set([makeSessionResponse(id: "s1", attention: nil)])
        await coordinator.reconcile()
        source.set(latched)
        await coordinator.reconcile()

        XCTAssertEqual(presenter.postedIds, ["s1", "s1"], "a re-latch after a clear alerts again")
    }

    func testObservedClearEventReArmsTheSession() async {
        seen.adoptBaseline([])
        let presenter = FakeAttentionPresenter()
        let (events, continuation) = AsyncStream.makeStream(of: AgentEvent.self)
        let coordinator = makeCoordinator(
            presenter: presenter,
            events: events,
            snapshot: { [makeSessionResponse(id: "s1", attention: "turn_finished")] }
        )
        coordinator.start()

        continuation.yield(.sessionAttention(makeSessionResponse(id: "s1", attention: "turn_finished")))
        await waitUntil { presenter.postedIds == ["s1"] }
        continuation.yield(.sessionUpdated(makeSessionResponse(id: "s1", attention: nil)))
        await waitUntil { presenter.removed == ["s1"] }
        continuation.finish()
        await coordinator.reconcile()

        XCTAssertEqual(presenter.postedIds, ["s1", "s1"], "the session the user answered can alert again")
    }

    func testLiveAttentionIsNotRePostedByTheNextReconcile() async {
        // The alert the user just dismissed came from the live event path; the
        // reconcile must remember it all the same.
        seen.adoptBaseline([])
        let presenter = FakeAttentionPresenter()
        let (events, continuation) = AsyncStream.makeStream(of: AgentEvent.self)
        let coordinator = makeCoordinator(
            presenter: presenter,
            events: events,
            snapshot: { [makeSessionResponse(id: "s1", attention: "turn_finished")] }
        )
        coordinator.start()

        continuation.yield(.sessionAttention(makeSessionResponse(id: "s1", attention: "turn_finished")))
        await waitUntil { presenter.postedIds == ["s1"] }
        continuation.finish()
        await coordinator.reconcile()

        XCTAssertEqual(presenter.postedIds, ["s1"])
    }

    // MARK: - What must not be remembered

    func testDeliveredInstanceIsSkippedWithoutBeingRecorded() async {
        // A still-visible alert needs no re-post, but its identifier may belong
        // to an older instance delivered by APNs — recording it would mute an
        // instance this device never rendered.
        seen.adoptBaseline([])
        let presenter = FakeAttentionPresenter(delivered: ["s1"])
        let coordinator = makeCoordinator(
            presenter: presenter,
            snapshot: { [makeSessionResponse(id: "s1", attention: "turn_finished")] }
        )

        await coordinator.reconcile()
        XCTAssertTrue(presenter.posted.isEmpty, "an alert already on screen is not re-posted")

        presenter.setDelivered([])
        await coordinator.reconcile()
        XCTAssertEqual(presenter.postedIds, ["s1"], "dismissing it falls through to a single post")
    }

    func testDroppedPostIsRetriedByTheNextReconcile() async {
        // `post` drops while authorization is undecided; remembering that as
        // surfaced would defeat the grant-driven recovery reconcile.
        seen.adoptBaseline([])
        let presenter = FakeAttentionPresenter(postSucceeds: false)
        let coordinator = makeCoordinator(
            presenter: presenter,
            snapshot: { [makeSessionResponse(id: "s1", attention: "turn_finished")] }
        )

        await coordinator.reconcile()
        presenter.setPostSucceeds(true)
        await coordinator.reconcile()
        await coordinator.reconcile()

        XCTAssertEqual(presenter.postedIds, ["s1", "s1"], "retried until it lands, then remembered")
    }

    // MARK: - Teardown scope

    func testSignOutForgetsSurfacedAttention() async {
        let session = makeSessionResponse(id: "s1", attention: "turn_finished")
        seen.adoptBaseline([session])
        let presenter = FakeAttentionPresenter()
        let (authStates, continuation) = AsyncStream.makeStream(of: AuthState.self)
        let coordinator = makeCoordinator(presenter: presenter, authStates: authStates)
        coordinator.start()

        continuation.yield(.unauthenticated)
        continuation.finish()
        await waitUntil { presenter.removeAllCount == 1 }

        XCTAssertFalse(seen.hasSurfaced(session), "the memory leaves with the account")
        XCTAssertFalse(seen.isBaselined)
    }

    func testAgentSwitchKeepsSurfacedAttention() async {
        // Those alerts were genuinely shown; re-selecting the agent must not
        // replay them.
        let session = makeSessionResponse(id: "s1", attention: "turn_finished")
        seen.adoptBaseline([session])
        let presenter = FakeAttentionPresenter()
        let (authStates, continuation) = AsyncStream.makeStream(of: AuthState.self)
        let coordinator = makeCoordinator(
            presenter: presenter,
            authStates: authStates,
            snapshot: { [session] }
        )
        coordinator.start()

        continuation.yield(.fullyConnected)
        continuation.yield(.userOnly)
        continuation.finish()
        await waitUntil { presenter.removeAllCount == 1 }

        XCTAssertTrue(seen.hasSurfaced(session))
        XCTAssertTrue(seen.isBaselined)
    }
}
