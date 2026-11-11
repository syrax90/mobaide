import UIKit
import XCTest
@testable import Mobaide

@MainActor
final class TerminalPanScrollViewTests: XCTestCase {
    private func makeViews(
        mode: TerminalDisplayMode,
        staticColumns: Int = 120
    ) -> (scroll: TerminalPanScrollView, terminal: MobaideTerminalView) {
        let terminal = MobaideTerminalView(frame: .zero)
        terminal.configureAppearance()
        let scroll = TerminalPanScrollView(
            terminalView: terminal,
            displayConfig: TerminalDisplayConfig(mode: mode, staticColumns: staticColumns)
        )
        return (scroll, terminal)
    }

    // MARK: - shouldClaimHorizontalPan

    func testClaimsHorizontallyDominantVelocityEvenWithZeroTranslation() {
        // The regression this guards: a UIScrollView pan can attempt to begin
        // while translation is still .zero, so a translation-only check read a
        // horizontal drag as a vertical tie and refused the pan for the touch.
        XCTAssertTrue(TerminalPanScrollView.shouldClaimHorizontalPan(
            velocity: CGPoint(x: 200, y: 30), translation: .zero, isDecelerating: false
        ))
    }

    func testVelocityTakesPrecedenceOverContradictoryTranslation() {
        XCTAssertFalse(TerminalPanScrollView.shouldClaimHorizontalPan(
            velocity: CGPoint(x: 0, y: 50), translation: CGPoint(x: 80, y: 0), isDecelerating: false
        ))
    }

    func testStationaryTouchClaimsOnlyWhileDecelerating() {
        // Tap-to-stop must catch an in-flight fling; a plain stationary touch
        // must not steal the terminal's gestures.
        XCTAssertTrue(TerminalPanScrollView.shouldClaimHorizontalPan(
            velocity: .zero, translation: .zero, isDecelerating: true
        ))
        XCTAssertFalse(TerminalPanScrollView.shouldClaimHorizontalPan(
            velocity: .zero, translation: .zero, isDecelerating: false
        ))
    }

    // MARK: - Axis arbitration

    func testInnerTerminalCarriesNoDirectionalLock() {
        for mode in TerminalDisplayMode.allCases {
            let (scroll, terminal) = makeViews(mode: mode)

            XCTAssertFalse(
                terminal.isDirectionalLockEnabled,
                "\(mode): the lock latched the inner view onto whichever axis started dominant — including the horizontal one, where it has no overflow to scroll and the scrollback stayed dead for the rest of the touch"
            )
            XCTAssertTrue(
                scroll.isDirectionalLockEnabled,
                "\(mode): only the inner lock is gone; the outer keeps its own"
            )
        }
    }

    func testIdleOuterClaimsNothingSoTheInnerKeepsTheTouch() {
        for mode in TerminalDisplayMode.allCases {
            let (scroll, _) = makeViews(mode: mode)

            XCTAssertFalse(
                scroll.claimsHorizontalPan(),
                "\(mode): a resting recognizer is no drag to claim, and the inner asks this on every touch"
            )
        }
    }

    func testAmbiguousSampleYieldsTheTouchInsteadOfDeadLockingIt() {
        let (scroll, _) = makeViews(mode: .static)

        XCTAssertFalse(
            scroll.wouldClaimHorizontalPan(velocity: .zero, translation: .zero),
            "the deliberate divergence from the outer's own form: a sample carrying no direction must not refuse the inner's pan, or the touch is dead on both views"
        )
    }

    func testStaticModeStillRefusesTheHorizontalPanItDoesNotClaim() {
        let (scroll, _) = makeViews(mode: .static)

        XCTAssertFalse(
            scroll.gestureRecognizerShouldBegin(scroll.panGestureRecognizer),
            "the outer's own refusal has to survive being rerouted through claimsHorizontalPan()"
        )
    }

    // MARK: - Layout

    func testFullModeSizesTerminalToViewport() {
        let (scroll, terminal) = makeViews(mode: .full)
        scroll.frame = CGRect(x: 0, y: 0, width: 390, height: 600)

        scroll.layoutIfNeeded()

        XCTAssertEqual(terminal.frame, CGRect(x: 0, y: 0, width: 390, height: 600))
        XCTAssertEqual(scroll.contentSize, CGSize(width: 390, height: 600))
    }

    func testStaticModeSizesTerminalToColumnWidthBeyondViewport() {
        let (scroll, terminal) = makeViews(mode: .static, staticColumns: 120)
        scroll.frame = CGRect(x: 0, y: 0, width: 390, height: 600)

        scroll.layoutIfNeeded()
        // Second pass with a settled cellSize: the first layout can update
        // SwiftTerm's caret metrics, which cellSize is derived from.
        scroll.setNeedsLayout()
        scroll.layoutIfNeeded()

        let expectedWidth = ceil(120 * terminal.cellSize.width)
        XCTAssertGreaterThan(expectedWidth, 390, "120 columns must overflow a phone viewport")
        XCTAssertEqual(terminal.frame.width, expectedWidth)
        XCTAssertEqual(terminal.frame.height, 600, "vertical axis stays pinned to the viewport")
        XCTAssertEqual(scroll.contentSize, CGSize(width: expectedWidth, height: 600))
    }

    func testLayoutSkipsGeometryWritesAfterTerminalIsReparented() {
        let (scroll, terminal) = makeViews(mode: .full)
        scroll.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        scroll.layoutIfNeeded()

        let thief = UIView(frame: CGRect(x: 0, y: 0, width: 200, height: 200))
        thief.addSubview(terminal)
        let stolenFrame = CGRect(x: 3, y: 7, width: 50, height: 60)
        terminal.frame = stolenFrame

        scroll.setNeedsLayout()
        scroll.layoutIfNeeded()

        XCTAssertEqual(
            terminal.frame, stolenFrame,
            "a stale wrapper must not write geometry it no longer owns"
        )
    }

    // MARK: - Keyboard counter-pan

    func testKeyboardLiftNeverChangesTerminalSizeOrContentSize() {
        let (scroll, terminal) = makeViews(mode: .full)
        scroll.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        scroll.layoutIfNeeded()
        let restSize = terminal.frame.size
        let restContent = scroll.contentSize

        scroll.caretAnchorBottomOverride = 100 // high caret → maximum counter-pan
        scroll.setKeyboardLift(300, duration: 0)
        scroll.layoutIfNeeded()

        XCTAssertEqual(terminal.frame.size, restSize, "a bounds-size change would fire sizeChanged → a PTY `r` frame")
        XCTAssertEqual(scroll.contentSize, restContent)
    }

    func testCounterPanOffsetsTerminalFrameDownward() {
        let (scroll, terminal) = makeViews(mode: .full)
        scroll.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        scroll.layoutIfNeeded()

        scroll.caretAnchorBottomOverride = 450
        scroll.setKeyboardLift(300, duration: 0)
        scroll.layoutIfNeeded()

        XCTAssertEqual(terminal.frame.origin.y, 150, "dy = boxHeight − anchor = 600 − 450")
        XCTAssertEqual(terminal.frame.origin.x, 0)
    }

    func testCounterPanClampsToLift() {
        let (scroll, terminal) = makeViews(mode: .full)
        scroll.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        scroll.layoutIfNeeded()

        scroll.caretAnchorBottomOverride = 100 // boxHeight − anchor = 500 > lift
        scroll.setKeyboardLift(300, duration: 0)
        scroll.layoutIfNeeded()

        XCTAssertEqual(terminal.frame.origin.y, 300, "compensation never exceeds the lift")
    }

    func testKeyboardHideRestoresRestOrigin() {
        let (scroll, terminal) = makeViews(mode: .full)
        scroll.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        scroll.layoutIfNeeded()
        scroll.caretAnchorBottomOverride = 450
        scroll.setKeyboardLift(300, duration: 0)
        scroll.layoutIfNeeded()
        XCTAssertEqual(terminal.frame.origin.y, 150)

        scroll.setKeyboardLift(0, duration: 0)
        scroll.layoutIfNeeded()

        XCTAssertEqual(terminal.frame.origin.y, 0)
    }
}
