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

    func testZeroDurationApplyStillUpdatesTransition() {
        let observer = TerminalKeyboardObserver()
        observer.apply(endFrameInWindow: dockedKeyboard, windowBounds: windowBounds, bottomSafeInset: 34, duration: 0.3)
        XCTAssertEqual(observer.transition.lift, 310)

        // Hardware-keyboard attach: the software keyboard collapses to the
        // assistant bar with a zero-duration frame change.
        let assistantBar = CGRect(x: 0, y: 775, width: 390, height: 69)
        observer.apply(endFrameInWindow: assistantBar, windowBounds: windowBounds, bottomSafeInset: 34, duration: 0)

        XCTAssertEqual(
            observer.transition,
            .init(lift: 35, duration: 0),
            "a zero-duration frame change must still snap the lift, not be dropped by the animated path split"
        )
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
