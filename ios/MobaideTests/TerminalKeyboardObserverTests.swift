import UIKit
import XCTest
@testable import Mobaide

/// Covers `TerminalKeyboardObserver.apply` (the notification glue extracted
/// for direct testing): geometry → published `Transition`, the equality guard,
/// and the unanimated `stop()` reset.
@MainActor
final class TerminalKeyboardObserverTests: XCTestCase {
    private final class Flag: @unchecked Sendable {
        var raised = false
    }

    private let windowBounds = CGRect(x: 0, y: 0, width: 390, height: 844)
    private let dockedKeyboard = CGRect(x: 0, y: 500, width: 390, height: 344)

    func testApplyComputesLiftFromGeometry() {
        let observer = TerminalKeyboardObserver()

        observer.apply(
            endFrameInWindow: dockedKeyboard,
            windowBounds: windowBounds,
            bottomSafeInset: 34,
            duration: 0.3
        )

        XCTAssertEqual(observer.transition, .init(lift: 310, duration: 0.3))
    }

    func testEqualTransitionDoesNotRepublish() {
        let observer = TerminalKeyboardObserver()
        observer.apply(endFrameInWindow: dockedKeyboard, windowBounds: windowBounds, bottomSafeInset: 34, duration: 0.3)
        let changed = Flag()
        withObservationTracking {
            _ = observer.transition
        } onChange: {
            changed.raised = true
        }

        observer.apply(endFrameInWindow: dockedKeyboard, windowBounds: windowBounds, bottomSafeInset: 34, duration: 0.3)
        XCTAssertFalse(
            changed.raised,
            "an identical transition must not republish — every publish re-runs the screen's offset animation"
        )

        // Same tracking registration must still be armed: a real change fires it.
        observer.apply(endFrameInWindow: dockedKeyboard, windowBounds: windowBounds, bottomSafeInset: 34, duration: 0.1)
        XCTAssertTrue(changed.raised)
    }

    func testStopResetsTransitionUnanimated() {
        let observer = TerminalKeyboardObserver()
        observer.apply(endFrameInWindow: dockedKeyboard, windowBounds: windowBounds, bottomSafeInset: 34, duration: 0.3)
        XCTAssertEqual(observer.transition.lift, 310)

        observer.stop()

        XCTAssertEqual(
            observer.transition,
            .init(lift: 0, duration: 0),
            "leaving the screen must snap the lift back with a zero-duration transition"
        )
    }
}
