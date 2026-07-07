import XCTest
@testable import Mobaide

final class MiscAuthTests: XCTestCase {
    func testGenerateNonceIs64CharLowercaseHexAndUnique() {
        let first = AuthNonce.generateNonce()
        let second = AuthNonce.generateNonce()

        XCTAssertEqual(first.count, 64)
        XCTAssertTrue(first.allSatisfy { $0.isHexDigit && (!$0.isLetter || $0.isLowercase) })
        XCTAssertNotEqual(first, second)
    }

    func testCentralServerURLResolution() {
        XCTAssertEqual(AppConfig.resolveCentralServerURL(nil), AppConfig.defaultCentralServerURL)
        XCTAssertEqual(AppConfig.resolveCentralServerURL(""), AppConfig.defaultCentralServerURL)
        XCTAssertEqual(
            AppConfig.resolveCentralServerURL("$(CENTRAL_SERVER_URL)"),
            AppConfig.defaultCentralServerURL,
            "an unresolved Info.plist placeholder must not be treated as a configured URL"
        )
        XCTAssertEqual(
            AppConfig.resolveCentralServerURL("https://custom.example.com"),
            "https://custom.example.com"
        )
    }

    func testForegroundSessionTrackerCompareAndSetClear() {
        let tracker = ForegroundSessionTracker()
        XCTAssertNil(tracker.visibleSessionId)

        tracker.setVisible("s1")
        XCTAssertEqual(tracker.visibleSessionId, "s1")

        tracker.clearVisible("other")
        XCTAssertEqual(tracker.visibleSessionId, "s1", "stale clear must not wipe a newer registration")

        tracker.clearVisible("s1")
        XCTAssertNil(tracker.visibleSessionId)
    }
}
