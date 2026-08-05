import XCTest
@testable import Mobaide

@MainActor
final class AttentionSeenStoreTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!
    private var preferences: PreferencesStore!

    override func setUp() {
        super.setUp()
        suiteName = "attention-seen-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        preferences = PreferencesStore(defaults: defaults)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private func makeStore(agentKey: String? = "agent-1") -> AttentionSeenStore {
        AttentionSeenStore(preferences: preferences, agentKey: { agentKey })
    }

    func testRecordedInstanceIsSurfacedOnlyForThatSession() {
        let store = makeStore()
        let s1 = makeSessionResponse(id: "s1", attention: "turn_finished")
        let s2 = makeSessionResponse(id: "s2", attention: "turn_finished")

        XCTAssertFalse(store.hasSurfaced(s1))
        store.record(s1)

        XCTAssertTrue(store.hasSurfaced(s1))
        XCTAssertFalse(store.hasSurfaced(s2), "one session's alert says nothing about another's")
    }

    func testChangedReasonOrExitReasonIsANewInstance() {
        // `set_attention` emits again when either changes — a `/clear`
        // `SessionEnd` leaves the PTY running, so a second ending is real news.
        let store = makeStore()
        store.record(makeSessionResponse(id: "s1", attention: "ended", exitReason: "completed"))

        XCTAssertTrue(
            store.hasSurfaced(makeSessionResponse(id: "s1", attention: "ended", exitReason: "completed"))
        )
        XCTAssertFalse(
            store.hasSurfaced(makeSessionResponse(id: "s1", attention: "ended", exitReason: "exit code 1")),
            "a different exit reason is a genuinely new edge"
        )
        XCTAssertFalse(
            store.hasSurfaced(makeSessionResponse(id: "s1", attention: "turn_finished")),
            "a different reason is a genuinely new edge"
        )
    }

    func testForgetReArmsOneSessionAndLeavesTheRest() {
        let store = makeStore()
        let s1 = makeSessionResponse(id: "s1", attention: "turn_finished")
        let s2 = makeSessionResponse(id: "s2", attention: "awaiting_permission")
        store.record(s1)
        store.record(s2)

        store.forget(sessionId: "s1")

        XCTAssertFalse(store.hasSurfaced(s1))
        XCTAssertTrue(store.hasSurfaced(s2))
    }

    func testRetainDropsEverySessionOutsideTheAttentionSet() {
        // Sessions whose attention cleared (or that were deleted) while this
        // device was away are re-armed by the snapshot alone.
        let store = makeStore()
        let kept = makeSessionResponse(id: "kept", attention: "turn_finished")
        let cleared = makeSessionResponse(id: "cleared", attention: "turn_finished")
        store.record(kept)
        store.record(cleared)

        store.retain(sessionIds: ["kept"])

        XCTAssertTrue(store.hasSurfaced(kept))
        XCTAssertFalse(store.hasSurfaced(cleared))
    }

    func testAdoptBaselineRecordsEveryLatchedSessionAndSettlesTheFlag() {
        let store = makeStore()
        let s1 = makeSessionResponse(id: "s1", attention: "turn_finished")
        let s2 = makeSessionResponse(id: "s2", attention: "ended", exitReason: "completed")
        XCTAssertFalse(store.isBaselined)

        store.adoptBaseline([s1, s2])

        XCTAssertTrue(store.isBaselined)
        XCTAssertTrue(store.hasSurfaced(s1))
        XCTAssertTrue(store.hasSurfaced(s2))
    }

    func testRecordSurvivesAcrossStoreInstances() {
        // The whole point of the store: a relaunch must not replay the alert.
        let session = makeSessionResponse(id: "s1", attention: "turn_finished")
        makeStore().record(session)

        let afterRelaunch = makeStore()

        XCTAssertTrue(afterRelaunch.hasSurfaced(session))
    }

    func testBucketsAreScopedPerAgentAndClearAllDropsThemAll() {
        let session = makeSessionResponse(id: "s1", attention: "turn_finished")
        let first = makeStore(agentKey: "agent-1")
        let second = makeStore(agentKey: "agent-2")
        first.adoptBaseline([session])

        XCTAssertFalse(second.hasSurfaced(session), "notifications are agent-scoped")
        XCTAssertFalse(second.isBaselined, "a second agent takes its own baseline")

        second.record(session)
        first.clearAll()

        XCTAssertFalse(first.hasSurfaced(session))
        XCTAssertFalse(first.isBaselined)
        XCTAssertFalse(second.hasSurfaced(session), "sign-out clears every agent's bucket")
    }

    func testMissingAgentKeyIsInert() {
        // The agent id can already be gone from the keychain when sign-out
        // reaches here; the store must no-op rather than write a junk bucket.
        let store = makeStore(agentKey: nil)
        let session = makeSessionResponse(id: "s1", attention: "turn_finished")

        store.record(session)
        store.adoptBaseline([session])

        XCTAssertFalse(store.hasSurfaced(session))
        XCTAssertFalse(store.isBaselined)
    }
}
