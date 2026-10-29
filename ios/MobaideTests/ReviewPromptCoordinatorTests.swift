import XCTest
@testable import Mobaide

/// When an outing counts, when the settle wait gives up, and when a request is
/// made and delivered. The counters live in a real `PreferencesStore` over an
/// isolated suite and are read back from it.
@MainActor
final class ReviewPromptCoordinatorTests: XCTestCase {
    private static let t0 = Date(timeIntervalSince1970: 1_793_275_200)
    private static let day: TimeInterval = 24 * 60 * 60

    private var suiteName: String!
    private var defaults: UserDefaults!
    private var store: PreferencesStore!
    private var tracker: ForegroundSessionTracker!
    private var appActive = true
    private var now = ReviewPromptCoordinatorTests.t0

    override func setUp() {
        super.setUp()
        suiteName = "review-prompt-tests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        store = PreferencesStore(defaults: defaults)
        tracker = ForegroundSessionTracker()
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private func makeCoordinator(sleeper: any Sleeper = RecordingSleeper()) -> ReviewPromptCoordinator {
        ReviewPromptCoordinator(
            preferencesStore: store,
            foregroundSessionTracker: tracker,
            sleeper: sleeper,
            isAppActive: { [unowned self] in self.appActive },
            now: { [unowned self] in self.now }
        )
    }

    private func seed(launches: Int, goodSessions: Int = 0, lastRequestedAt: Date? = nil) {
        for _ in 0..<launches { store.incrementReviewLaunchCount() }
        if let lastRequestedAt { store.markReviewRequested(at: lastRequestedAt) }
        for _ in 0..<goodSessions { _ = store.incrementReviewGoodSessions() }
    }

    private var goodSessions: Int {
        defaults.integer(forKey: PreferencesStore.keyReviewGoodSessions)
    }

    private var lastRequestedAt: Date? {
        (defaults.object(forKey: PreferencesStore.keyReviewLastRequestedAt) as? Double)
            .map { Date(timeIntervalSince1970: $0) }
    }

    private func goodVisit(_ coordinator: ReviewPromptCoordinator) {
        coordinator.recordTerminalVisit(healthySeconds: 60, endedCleanly: true)
    }

    // MARK: - Counting good outings

    func testGoodOutingsCountAndTheThirdRequestsAReview() async {
        seed(launches: 2)
        let coordinator = makeCoordinator()

        for expected in 1...2 {
            goodVisit(coordinator)
            await coordinator.sessionListShown()
            XCTAssertEqual(goodSessions, expected)
        }
        XCTAssertEqual(coordinator.requestCount, 0)

        goodVisit(coordinator)
        await coordinator.sessionListShown()

        XCTAssertEqual(coordinator.requestCount, 1)
        XCTAssertEqual(lastRequestedAt, Self.t0)
        XCTAssertEqual(goodSessions, 0)
    }

    func testVisitsInOneOutingSumTheirHealthyTime() async {
        let coordinator = makeCoordinator()

        coordinator.recordTerminalVisit(healthySeconds: 30, endedCleanly: true)
        coordinator.recordTerminalVisit(healthySeconds: 30, endedCleanly: true)
        await coordinator.sessionListShown()

        XCTAssertEqual(goodSessions, 1)
    }

    func testAnUncleanVisitSpoilsTheWholeOutingAndTheNextOutingStartsClean() async {
        let coordinator = makeCoordinator()

        coordinator.recordTerminalVisit(healthySeconds: 120, endedCleanly: true)
        coordinator.recordTerminalVisit(healthySeconds: 0, endedCleanly: false)
        await coordinator.sessionListShown()
        XCTAssertEqual(goodSessions, 0)

        goodVisit(coordinator)
        await coordinator.sessionListShown()
        XCTAssertEqual(goodSessions, 1)
    }

    // MARK: - Errors

    func testNoteErrorSpoilsTheOpenOuting() async {
        let coordinator = makeCoordinator()

        goodVisit(coordinator)
        coordinator.noteError()
        await coordinator.sessionListShown()

        XCTAssertEqual(goodSessions, 0)
    }

    func testNoteErrorDuringTheFirstOpenVisitSpoilsTheOuting() async {
        let coordinator = makeCoordinator()

        coordinator.terminalVisitStarted()
        coordinator.noteError()
        goodVisit(coordinator)
        await coordinator.sessionListShown()

        XCTAssertEqual(goodSessions, 0)
    }

    func testNoteErrorWithNoOutingOpenSpoilsNothing() async {
        let coordinator = makeCoordinator()

        coordinator.noteError()
        goodVisit(coordinator)
        await coordinator.sessionListShown()

        XCTAssertEqual(goodSessions, 1)
    }

    func testNoteErrorAfterTheOutingClosedSpoilsNothing() async {
        let coordinator = makeCoordinator()
        coordinator.terminalVisitStarted()
        goodVisit(coordinator)
        await coordinator.sessionListShown()

        coordinator.noteError()
        goodVisit(coordinator)
        await coordinator.sessionListShown()

        XCTAssertEqual(goodSessions, 2)
    }

    // MARK: - Settle wait

    func testATerminalVisibleAgainDuringTheSettleAbandonsAndTheOutingGoesOn() async {
        let sleeper = ManualSleeper()
        let coordinator = makeCoordinator(sleeper: sleeper)
        goodVisit(coordinator)

        let shown = Task { await coordinator.sessionListShown() }
        await waitUntil { sleeper.isWaiting }
        tracker.setVisible("s1")
        sleeper.tick()
        await shown.value
        XCTAssertEqual(goodSessions, 0)

        tracker.clearVisible("s1")
        sleeper.release()
        await coordinator.sessionListShown()
        XCTAssertEqual(goodSessions, 1)
    }

    func testAGitChildHoldingAWatchAbandonsAndTheOutingGoesOn() async {
        let coordinator = makeCoordinator()
        goodVisit(coordinator)

        tracker.holdWatch("s1")
        await coordinator.sessionListShown()
        XCTAssertEqual(goodSessions, 0)

        tracker.releaseWatch("s1")
        await coordinator.sessionListShown()
        XCTAssertEqual(goodSessions, 1)
    }

    func testCancellingTheSettleLeavesTheOutingOpen() async {
        let sleeper = ManualSleeper()
        let coordinator = makeCoordinator(sleeper: sleeper)
        goodVisit(coordinator)

        let shown = Task { await coordinator.sessionListShown() }
        await waitUntil { sleeper.isWaiting }
        shown.cancel()
        await shown.value
        XCTAssertEqual(goodSessions, 0)

        sleeper.release()
        await coordinator.sessionListShown()
        XCTAssertEqual(goodSessions, 1)
    }

    // MARK: - Delivery

    func testAnInactiveSceneNeitherStampsNorRequestsAndKeepsTheCount() async {
        seed(launches: 2, goodSessions: 2)
        appActive = false
        let coordinator = makeCoordinator()

        goodVisit(coordinator)
        await coordinator.sessionListShown()

        XCTAssertEqual(coordinator.requestCount, 0)
        XCTAssertNil(lastRequestedAt)
        XCTAssertEqual(goodSessions, 3)

        appActive = true
        goodVisit(coordinator)
        await coordinator.sessionListShown()

        XCTAssertEqual(coordinator.requestCount, 1)
        XCTAssertEqual(lastRequestedAt, Self.t0)
    }

    func testTheFirstLaunchNeverRequests() async {
        seed(launches: 1, goodSessions: 2)
        let coordinator = makeCoordinator()

        goodVisit(coordinator)
        await coordinator.sessionListShown()

        XCTAssertEqual(coordinator.requestCount, 0)
        XCTAssertNil(lastRequestedAt)
        XCTAssertEqual(goodSessions, 3)
    }

    func testTheCooldownIsMeasuredOnTheInjectedClock() async {
        seed(launches: 2, goodSessions: 2, lastRequestedAt: Self.t0.addingTimeInterval(-119 * Self.day))
        let coordinator = makeCoordinator()

        goodVisit(coordinator)
        await coordinator.sessionListShown()
        XCTAssertEqual(coordinator.requestCount, 0)

        now = Self.t0.addingTimeInterval(Self.day)
        goodVisit(coordinator)
        await coordinator.sessionListShown()

        XCTAssertEqual(coordinator.requestCount, 1)
        XCTAssertEqual(lastRequestedAt, now)
    }

    // MARK: - Launches

    func testRecordLaunchCountsOncePerProcess() {
        let coordinator = makeCoordinator()

        coordinator.recordLaunch()
        coordinator.recordLaunch()
        coordinator.recordLaunch()

        XCTAssertEqual(defaults.integer(forKey: PreferencesStore.keyReviewLaunchCount), 1)
    }
}
