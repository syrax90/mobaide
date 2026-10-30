import XCTest
@testable import Mobaide

/// The healthy-time threshold, the first-launch and good-session gates, and
/// the cooldown edges, including a stamp left in the future by a clock that
/// moved back.
final class ReviewPromptPolicyTests: XCTestCase {
    private static let now = Date(timeIntervalSince1970: 1_793_275_200)
    private static let day: TimeInterval = 24 * 60 * 60

    private func state(
        launchCount: Int = 2,
        goodSessions: Int = 3,
        lastRequestedAt: Date? = nil
    ) -> ReviewPromptState {
        ReviewPromptState(launchCount: launchCount, goodSessions: goodSessions, lastRequestedAt: lastRequestedAt)
    }

    func testAGoodOutingNeedsAFullMinuteOfHealthyTime() {
        XCTAssertFalse(ReviewPromptPolicy.isGoodOuting(healthySeconds: 59.999, allVisitsClean: true))
        XCTAssertTrue(ReviewPromptPolicy.isGoodOuting(healthySeconds: 60, allVisitsClean: true))
    }

    func testAnUncleanVisitIsNeverAGoodOuting() {
        XCTAssertFalse(ReviewPromptPolicy.isGoodOuting(healthySeconds: 3600, allVisitsClean: false))
    }

    func testTheFirstLaunchNeverAsks() {
        XCTAssertFalse(ReviewPromptPolicy.isEligible(state(launchCount: 1), now: Self.now))
    }

    func testTheSecondGoodSessionIsNotEnoughButTheThirdIs() {
        XCTAssertFalse(ReviewPromptPolicy.isEligible(state(goodSessions: 2), now: Self.now))
        XCTAssertTrue(ReviewPromptPolicy.isEligible(state(goodSessions: 3), now: Self.now))
    }

    func testTheCooldownEndsAfterExactly120Days() {
        let day119 = state(lastRequestedAt: Self.now.addingTimeInterval(-119 * Self.day))
        let day120 = state(lastRequestedAt: Self.now.addingTimeInterval(-120 * Self.day))

        XCTAssertFalse(ReviewPromptPolicy.isEligible(day119, now: Self.now))
        XCTAssertTrue(ReviewPromptPolicy.isEligible(day120, now: Self.now))
    }

    func testAStampInTheFutureIsIgnored() {
        let future = state(lastRequestedAt: Self.now.addingTimeInterval(Self.day))

        XCTAssertTrue(ReviewPromptPolicy.isEligible(future, now: Self.now))
    }
}
