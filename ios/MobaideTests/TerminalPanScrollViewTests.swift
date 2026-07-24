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

    func testRefusesVerticallyDominantVelocity() {
        XCTAssertFalse(TerminalPanScrollView.shouldClaimHorizontalPan(
            velocity: CGPoint(x: 30, y: -200), translation: .zero, isDecelerating: false
        ))
    }

    func testExactDiagonalTieYieldsToTerminal() {
        // Android parity: intercept iff dx > dy, ties fall through to scrollback.
        XCTAssertFalse(TerminalPanScrollView.shouldClaimHorizontalPan(
            velocity: CGPoint(x: 100, y: 100), translation: .zero, isDecelerating: false
        ))
    }

    func testVelocityTakesPrecedenceOverContradictoryTranslation() {
        XCTAssertFalse(TerminalPanScrollView.shouldClaimHorizontalPan(
            velocity: CGPoint(x: 0, y: 50), translation: CGPoint(x: 80, y: 0), isDecelerating: false
        ))
    }

    func testFallsBackToTranslationWhenVelocityIsZero() {
        XCTAssertTrue(TerminalPanScrollView.shouldClaimHorizontalPan(
            velocity: .zero, translation: CGPoint(x: 12, y: 4), isDecelerating: false
        ))
        XCTAssertFalse(TerminalPanScrollView.shouldClaimHorizontalPan(
            velocity: .zero, translation: CGPoint(x: 4, y: 12), isDecelerating: false
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

    // MARK: - displayConfig didSet

    func testReassigningEqualConfigStillReappliesBounceAndLayout() {
        let (scroll, _) = makeViews(mode: .static)
        scroll.alwaysBounceHorizontal = false

        scroll.displayConfig = TerminalDisplayConfig(mode: .static, staticColumns: 120)

        XCTAssertTrue(
            scroll.alwaysBounceHorizontal,
            "an equal-value assignment must still re-apply mode side effects"
        )
    }

    func testSwitchingToFullModeDisablesHorizontalBounce() {
        let (scroll, _) = makeViews(mode: .static)

        scroll.displayConfig = TerminalDisplayConfig(mode: .full, staticColumns: 120)

        XCTAssertFalse(scroll.alwaysBounceHorizontal)
    }
}
