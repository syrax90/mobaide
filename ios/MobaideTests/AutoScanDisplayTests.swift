import XCTest
@testable import Mobaide

/// Pure status/time derivations behind the autoscan cards (`AutoScanDisplay`).
/// The SwiftUI cards and the `statusColor`/`phaseColor` lookups are not tested
/// (Color equality is unreliable), matching `GitItemDisplayTests`.
final class AutoScanDisplayTests: XCTestCase {

    // MARK: - statusLabel

    func testStatusLabelForKnownStatuses() {
        XCTAssertEqual(AutoScanDisplay.statusLabel("scanning"), "Scanning")
        XCTAssertEqual(AutoScanDisplay.statusLabel("idle"), "Idle")
        XCTAssertEqual(AutoScanDisplay.statusLabel("stopped"), "Stopped")
        XCTAssertEqual(AutoScanDisplay.statusLabel("error"), "Error")
        XCTAssertEqual(AutoScanDisplay.statusLabel("starting"), "Starting")
    }

    func testStatusLabelCapitalizesUnknownStatus() {
        XCTAssertEqual(AutoScanDisplay.statusLabel("connecting"), "Connecting")
        XCTAssertEqual(AutoScanDisplay.statusLabel("replaying"), "Replaying")
    }

    func testStatusLabelEmptyStaysEmpty() {
        XCTAssertEqual(AutoScanDisplay.statusLabel(""), "")
    }

    // MARK: - relativeTime

    func testRelativeTimeNonPositiveEpochIsEmpty() {
        XCTAssertEqual(AutoScanDisplay.relativeTime(0), "")
        XCTAssertEqual(AutoScanDisplay.relativeTime(-1000), "")
    }

    func testRelativeTimeJustNowWithinFiveSeconds() {
        let now = Date().timeIntervalSince1970
        XCTAssertEqual(AutoScanDisplay.relativeTime(now - 2), "just now")
    }

    /// Offsets sit comfortably inside each bucket so ±1s scheduling jitter in
    /// the wall clock cannot cross a boundary or change the integer division.
    func testRelativeTimeBucketsMinutesHoursDays() {
        let now = Date().timeIntervalSince1970
        XCTAssertEqual(AutoScanDisplay.relativeTime(now - 150), "2m ago")
        XCTAssertEqual(AutoScanDisplay.relativeTime(now - 7300), "2h ago")
        XCTAssertEqual(AutoScanDisplay.relativeTime(now - 180_000), "2d ago")
    }
}
