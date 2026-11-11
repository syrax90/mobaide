import XCTest
@testable import Mobaide

final class AttentionSuppressionTests: XCTestCase {
    func testForegroundSameSessionSuppresses() {
        XCTAssertTrue(AttentionSuppression.shouldSuppress(appActive: true, visibleSessionId: "s1", eventSessionId: "s1"))
    }

    func testForegroundDifferentSessionShows() {
        XCTAssertFalse(AttentionSuppression.shouldSuppress(appActive: true, visibleSessionId: "s2", eventSessionId: "s1"))
    }

    func testBackgroundSameSessionShows() {
        XCTAssertFalse(AttentionSuppression.shouldSuppress(appActive: false, visibleSessionId: "s1", eventSessionId: "s1"))
    }
}
