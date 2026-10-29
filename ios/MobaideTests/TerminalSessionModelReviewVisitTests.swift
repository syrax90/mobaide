import XCTest
@testable import Mobaide

/// The model's meter reads the real uptime clock, so each test adds the
/// healthy minute as a later visit of the same outing. The user ends the
/// model's visit, which makes it clean without a live socket.
@MainActor
final class TerminalSessionModelReviewVisitTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!
    private var tracker: ForegroundSessionTracker!
    private var coordinator: ReviewPromptCoordinator!

    override func setUp() {
        super.setUp()
        suiteName = "review-visit-tests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        tracker = ForegroundSessionTracker()
        coordinator = ReviewPromptCoordinator(
            preferencesStore: PreferencesStore(defaults: defaults),
            foregroundSessionTracker: tracker,
            sleeper: RecordingSleeper(),
            isAppActive: { true }
        )
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private var goodSessions: Int {
        defaults.integer(forKey: PreferencesStore.keyReviewGoodSessions)
    }

    private func makeModel() -> TerminalSessionModel {
        TerminalTestSupport.model(foregroundSessionTracker: tracker, reviewPrompt: coordinator)
    }

    func testAVisitTheUserEndedLeavesTheOutingGood() async {
        let model = makeModel()

        model.markVisible()
        model.markEndedByUser()
        model.markHidden()
        coordinator.recordTerminalVisit(healthySeconds: 60, endedCleanly: true)
        await coordinator.sessionListShown()

        XCTAssertEqual(goodSessions, 1)
    }

    func testAnErrorShownDuringTheFirstVisitSpoilsTheOuting() async {
        let model = makeModel()

        // The first stop fails, the retry succeeds and the user leaves.
        model.markVisible()
        model.noteReviewError()
        model.markEndedByUser()
        model.markHidden()
        coordinator.recordTerminalVisit(healthySeconds: 60, endedCleanly: true)
        await coordinator.sessionListShown()

        XCTAssertEqual(goodSessions, 0)
    }
}
