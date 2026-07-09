import XCTest
@testable import Mobaide

@MainActor
final class AttentionCoordinatorTests: XCTestCase {
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
            snapshot: snapshot
        )
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

    func testSessionUpdatedWithAttentionIsIgnored() async {
        let presenter = FakeAttentionPresenter()
        let (events, continuation) = AsyncStream.makeStream(of: AgentEvent.self)
        let coordinator = makeCoordinator(presenter: presenter, events: events)
        coordinator.start()
        // The ignored update precedes a real attention post; once the post
        // lands, the ignored event was already processed (and skipped).
        continuation.yield(.sessionUpdated(makeSessionResponse(id: "ignored", attention: "awaiting_permission")))
        continuation.yield(.sessionAttention(makeSessionResponse(id: "posted", attention: "turn_finished")))
        continuation.finish()
        await waitUntil { presenter.postedIds == ["posted"] }
        XCTAssertEqual(presenter.postedIds, ["posted"])
        XCTAssertTrue(presenter.removed.isEmpty)
    }

    func testReconcilePostsMissingAndRemovesStale() async {
        // A (snapshot attention set) = {a, c}; B (delivered) = {c, d}.
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

    func testProvisionalAuthorizationRequestedOnceOnFirstFullyConnected() async {
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

    func testReconcileSkipsWhenSnapshotFails() async {
        // A transient REST failure must not be read as an empty attention set;
        // otherwise reconcile would remove every delivered notification.
        struct SnapshotFailure: Error {}
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
}
