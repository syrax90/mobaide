import SwiftUI
import XCTest
@testable import Mobaide

/// The static ISO-8601 parsing behind `CountdownView` — nil/empty/malformed
/// guards plus the fractional-then-plain formatter fallback. The 1 Hz
/// `TimelineView` body itself is not tested (SwiftUI).
final class CountdownViewTests: XCTestCase {
    func testParseMalformedIsNil() {
        XCTAssertNil(CountdownView.parse("not-a-timestamp"))
    }

    func testParsePlainInternetDateTimeUsesFallbackFormatter() throws {
        let date = try XCTUnwrap(CountdownView.parse("1970-01-01T00:00:00Z"))
        XCTAssertEqual(date.timeIntervalSince1970, 0, accuracy: 0.001)
    }

    func testParseHonorsFractionalSeconds() throws {
        let date = try XCTUnwrap(CountdownView.parse("1970-01-01T00:00:00.500Z"))
        XCTAssertEqual(date.timeIntervalSince1970, 0.5, accuracy: 0.001)
    }
}
