import CoreGraphics
import XCTest
@testable import Mobaide

/// The Static-mode typing latch and the pan math behind horizontal cursor
/// follow. Must stay in step with Android `TerminalCursorRevealTest`: same
/// viewport (400 pt, 10 pt cells → 2-cell margin = 20 pt, quarter-viewport
/// inset = 100 pt) and same expected offsets.
@MainActor
final class TerminalCursorRevealTests: XCTestCase {
    private var now: TimeInterval = 10
    private lazy var reveal = TerminalCursorReveal(now: { [unowned self] in self.now })

    private func target(
        cursorX: CGFloat,
        cellWidth: CGFloat = 10,
        viewportWidth: CGFloat = 400,
        contentWidth: CGFloat = 1200,
        currentOffsetX: CGFloat = 0
    ) -> CGFloat? {
        TerminalCursorReveal.targetOffsetX(
            cursorX: cursorX,
            cellWidth: cellWidth,
            viewportWidth: viewportWidth,
            contentWidth: contentWidth,
            currentOffsetX: currentOffsetX
        )
    }

    // MARK: - Latch

    func testArmedLatchWaitsForTheCursorToLeaveWhereTheKeystrokeFoundIt() {
        reveal.arm(col: 5, row: 2)

        XCTAssertTrue(reveal.isArmed)
        XCTAssertFalse(
            reveal.shouldReveal(col: 5, row: 2),
            "a TUI that parks a hidden cursor never moves it — revealing there would drag the view on every key"
        )
        XCTAssertTrue(reveal.shouldReveal(col: 6, row: 2), "the echo moved the cursor right")
        XCTAssertTrue(reveal.shouldReveal(col: 5, row: 3), "Enter moved it to another row")
    }

    func testWindowClosesTwoSecondsAfterTheLastKeystroke() {
        reveal.arm(col: 0, row: 0)

        now += TerminalCursorReveal.window - 0.25
        XCTAssertTrue(reveal.shouldReveal(col: 1, row: 0))

        now += 0.25
        XCTAssertFalse(reveal.isArmed, "passive output after the window must never pan the view")
        XCTAssertFalse(reveal.shouldReveal(col: 1, row: 0))
    }

    func testEachKeystrokeExtendsTheWindowButKeepsTheFirstOrigin() {
        reveal.arm(col: 0, row: 0)
        now += 1.5
        reveal.arm(col: 1, row: 0)
        now += 1.5

        XCTAssertTrue(reveal.isArmed, "a burst of keys keeps the window open past the first deadline")
        XCTAssertTrue(
            reveal.shouldReveal(col: 1, row: 0),
            "the origin is the burst's first cursor, so col 1 still counts as moved"
        )
        XCTAssertTrue(
            reveal.shouldReveal(col: 0, row: 0),
            "once the burst has moved, a return to the first origin is followed too"
        )
    }

    func testArmingAfterTheWindowLapsedCapturesAFreshOrigin() {
        reveal.arm(col: 0, row: 0)
        now += TerminalCursorReveal.window
        reveal.arm(col: 9, row: 4)

        XCTAssertFalse(reveal.shouldReveal(col: 9, row: 4))
        XCTAssertTrue(reveal.shouldReveal(col: 0, row: 0))
    }

    func testBurstThatLapsedForgetsItHadMoved() {
        reveal.arm(col: 0, row: 0)
        XCTAssertTrue(reveal.shouldReveal(col: 9, row: 0))
        now += TerminalCursorReveal.window

        reveal.arm(col: 0, row: 0)

        XCTAssertFalse(
            reveal.shouldReveal(col: 0, row: 0),
            "a fresh burst starts unmoved, so a parked cursor is left alone again"
        )
    }

    func testDisarmForgetsTheBurstHadMoved() {
        reveal.arm(col: 0, row: 0)
        XCTAssertTrue(reveal.shouldReveal(col: 9, row: 0))
        reveal.disarm()

        reveal.arm(col: 0, row: 0)

        XCTAssertFalse(reveal.shouldReveal(col: 0, row: 0))
    }

    // MARK: - Pan math

    func testNoOverflowMeansNothingToPan() {
        XCTAssertNil(target(cursorX: 900, contentWidth: 400), "Full mode: content fits the viewport")
        XCTAssertNil(target(cursorX: 900, contentWidth: 300))
    }

    func testZeroCellWidthPansNowhere() {
        XCTAssertNil(target(cursorX: 900, cellWidth: 0))
    }

    func testVisibleCursorLeavesTheViewAlone() {
        XCTAssertNil(target(cursorX: 200))
        XCTAssertNil(target(cursorX: 700, currentOffsetX: 500), "scrolled right, cursor mid-viewport")
    }

    func testRightMarginIsInclusiveOfTwoCells() {
        // Cursor right edge 380 == 400 − 20: still inside.
        XCTAssertNil(target(cursorX: 370))
        // One point more crosses the margin and jumps a quarter viewport past the edge.
        XCTAssertEqual(target(cursorX: 371), 81)
    }

    func testRightJumpClampsToTheEndOfTheContent() {
        // Raw target 1190 − 300 = 890, max offset 1200 − 400 = 800.
        XCTAssertEqual(target(cursorX: 1180), 800)
    }

    func testLeftMarginIsInclusiveOfTwoCells() {
        XCTAssertNil(target(cursorX: 520, currentOffsetX: 500))
        XCTAssertEqual(target(cursorX: 519, currentOffsetX: 500), 419)
    }

    func testCarriageReturnToColumnZeroPansFullyBack() {
        XCTAssertEqual(target(cursorX: 0, currentOffsetX: 500), 0)
    }
}
