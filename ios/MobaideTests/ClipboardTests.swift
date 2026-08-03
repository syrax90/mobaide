import XCTest
import UIKit
@testable import Mobaide

final class ClipboardTests: XCTestCase {
    @MainActor
    func testReadTextReturnsNilForAnEmptyPasteboard() {
        UIPasteboard.general.items = []

        XCTAssertFalse(Clipboard.hasText)
        XCTAssertNil(Clipboard.readText())
    }

    @MainActor
    func testReadTextRoundTripsAStringAndAgreesWithHasText() {
        UIPasteboard.general.string = "/usr/local/bin"

        XCTAssertTrue(Clipboard.hasText)
        XCTAssertEqual(Clipboard.readText(), "/usr/local/bin")
    }

    @MainActor
    func testEmptyStringIsTreatedAsNoText() {
        UIPasteboard.general.string = ""

        XCTAssertNil(Clipboard.readText(), "an empty clip must be a silent no-op, not a zero-byte paste")
    }

    func testSensitiveOptionsSetLocalOnlyAndExpiration() throws {
        let now = Date(timeIntervalSince1970: 1_000_000)
        let options = Clipboard.sensitiveOptions(now: now)

        XCTAssertEqual(options[.localOnly] as? Bool, true)
        let expiration = try XCTUnwrap(options[.expirationDate] as? Date)
        XCTAssertEqual(expiration.timeIntervalSince1970, now.timeIntervalSince1970 + 60, accuracy: 0.001)
    }
}
