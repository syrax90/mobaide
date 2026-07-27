import CoreGraphics
import XCTest
@testable import Mobaide

/// Pure math of the pan-don't-resize keyboard layout (Android `ImeFloatingPan`
/// / `imeCursorCounterPan` parity). Net visual pan is `lift - counterPan`:
/// full lift when the caret sits at the grid bottom, zero when it sits high.
final class TerminalKeyboardPanTests: XCTestCase {
    private let windowBounds = CGRect(x: 0, y: 0, width: 390, height: 844)

    // MARK: - lift

    func testLiftIsKeyboardOverlapMinusBottomInset() {
        let lift = TerminalKeyboardPan.lift(
            keyboardEndFrame: CGRect(x: 0, y: 500, width: 390, height: 344),
            windowBounds: windowBounds,
            bottomSafeInset: 34
        )
        XCTAssertEqual(lift, 310, "844 - 500 - 34 — the overlap above the home-indicator line")
    }

    func testLiftClampsToZeroWhenKeyboardStaysBelowSafeInset() {
        let lift = TerminalKeyboardPan.lift(
            keyboardEndFrame: CGRect(x: 0, y: 830, width: 390, height: 14),
            windowBounds: windowBounds,
            bottomSafeInset: 34
        )
        XCTAssertEqual(lift, 0, "a sliver inside the safe inset must not produce a negative lift")
    }

    func testLiftZeroForDetachedFloatingKeyboard() {
        let lift = TerminalKeyboardPan.lift(
            keyboardEndFrame: CGRect(x: 20, y: 300, width: 350, height: 300),
            windowBounds: windowBounds,
            bottomSafeInset: 34
        )
        XCTAssertEqual(lift, 0, "an iPad floating keyboard does not touch the window bottom — no lift")
    }

    func testLiftZeroForNarrowSplitKeyboard() {
        let lift = TerminalKeyboardPan.lift(
            keyboardEndFrame: CGRect(x: 0, y: 500, width: 200, height: 344),
            windowBounds: windowBounds,
            bottomSafeInset: 34
        )
        XCTAssertEqual(lift, 0, "a keyboard narrower than the window (split) must not lift the column")
    }

    // MARK: - caretAnchorBottom

    func testAnchorAddsSlackRowsAndSubtractsContentOffset() {
        let anchor = TerminalKeyboardPan.caretAnchorBottom(
            caretFrame: CGRect(x: 0, y: 400, width: 9, height: 20),
            contentOffsetY: 100,
            cellHeight: 20
        )
        // (400 + 20) content-space bottom − 100 scroll offset + 2 slack rows × 20.
        XCTAssertEqual(anchor, 360)
    }

    func testUnrealizedCaretYieldsInfiniteAnchor() {
        let anchor = TerminalKeyboardPan.caretAnchorBottom(
            caretFrame: CGRect(x: 0, y: 0, width: 9, height: 0),
            contentOffsetY: 0,
            cellHeight: 20
        )
        XCTAssertEqual(anchor, .greatestFiniteMagnitude, "Android's Int.MAX_VALUE default — no counter-pan")
    }

    // MARK: - counterPan

    func testCounterPanZeroWhileKeyboardHidden() {
        XCTAssertEqual(
            TerminalKeyboardPan.counterPan(boxHeight: 600, caretAnchorBottom: 100, lift: 0),
            0,
            "no keyboard → no compensation, regardless of caret position"
        )
    }

    func testCounterPanZeroWhenCaretAtGridBottom() {
        XCTAssertEqual(
            TerminalKeyboardPan.counterPan(boxHeight: 600, caretAnchorBottom: 600, lift: 300),
            0,
            "a bottom caret needs the full lift to stay visible — nothing to give back"
        )
    }

    func testCounterPanClampsToLiftForHighCaret() {
        XCTAssertEqual(
            TerminalKeyboardPan.counterPan(boxHeight: 600, caretAnchorBottom: 100, lift: 300),
            300,
            "a high caret cancels the whole lift (net pan zero) but never overshoots it"
        )
    }

    func testCounterPanPinsCaretAnchorAtBoxBottom() {
        let boxHeight: CGFloat = 600
        let anchor: CGFloat = 450
        let lift: CGFloat = 300

        let dy = TerminalKeyboardPan.counterPan(boxHeight: boxHeight, caretAnchorBottom: anchor, lift: lift)

        XCTAssertEqual(dy, 150)
        // Net visual pan leaves the anchor exactly at the keyboard-shrunk
        // visible bottom: anchor − (boxHeight − lift).
        XCTAssertEqual(lift - dy, anchor - (boxHeight - lift))
    }

    func testUnrealizedCaretProducesZeroCounterPan() {
        XCTAssertEqual(
            TerminalKeyboardPan.counterPan(boxHeight: 600, caretAnchorBottom: .greatestFiniteMagnitude, lift: 300),
            0
        )
    }
}
