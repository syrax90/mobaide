import XCTest
@testable import Mobaide

/// `HealthyTimeMeter` on a hand-driven clock.
final class HealthyTimeMeterTests: XCTestCase {
    private final class Clock {
        var now: TimeInterval = 0
    }

    private let clock = Clock()
    private lazy var meter = HealthyTimeMeter(now: { [clock] in clock.now })

    func testSumsOnlyTheHealthySpans() {
        meter.update(healthy: true)
        clock.now = 10
        meter.update(healthy: false)
        clock.now = 25
        meter.update(healthy: true)
        clock.now = 40
        meter.update(healthy: false)

        XCTAssertEqual(meter.finish(), 25)
    }

    func testRepeatedHealthyUpdatesDoNotRestartTheSpan() {
        meter.update(healthy: true)
        clock.now = 5
        meter.update(healthy: true)
        clock.now = 12
        meter.update(healthy: false)

        XCTAssertEqual(meter.finish(), 12)
    }

    func testFinishClosesTheOpenSpan() {
        meter.update(healthy: true)
        clock.now = 61

        XCTAssertEqual(meter.finish(), 61)
    }

    func testFinishStartsTheNextVisitFromZero() {
        meter.update(healthy: true)
        clock.now = 30
        _ = meter.finish()

        clock.now = 50
        meter.update(healthy: true)
        clock.now = 52

        XCTAssertEqual(meter.finish(), 2)
    }
}
