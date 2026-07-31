import CoreGraphics
import XCTest
@testable import Mobaide

/// Pure routing math of the drag→wheel translation (Android
/// `TerminalView.doScroll` parity): which ladder arm owns a vertical drag,
/// which bytes that arm emits, and how drag pixels quantize into rows.
final class TerminalWheelScrollTests: XCTestCase {
    // MARK: - Ladder

    func testMouseReportingWinsOverAlternateBuffer() {
        XCTAssertEqual(
            TerminalWheelScroll.mode(isMouseReporting: true, isAlternateBuffer: true),
            .wheel,
            "Claude Code runs both at once; Android reports the wheel rather than moving anything locally"
        )
    }

    func testMouseReportingOnNormalBufferStillReportsWheel() {
        XCTAssertEqual(
            TerminalWheelScroll.mode(isMouseReporting: true, isAlternateBuffer: false),
            .wheel,
            "a mouse-reporting program owns the axis even on the normal buffer"
        )
    }

    func testAlternateBufferWithoutMouseReportingFallsBackToCursorKeys() {
        XCTAssertEqual(
            TerminalWheelScroll.mode(isMouseReporting: false, isAlternateBuffer: true),
            .cursorKeys,
            "`less`/`vim` sit on the alternate screen with no mouse tracking and no scrollback"
        )
    }

    func testPlainShellKeepsLocalScrollback() {
        XCTAssertEqual(
            TerminalWheelScroll.mode(isMouseReporting: false, isAlternateBuffer: false),
            .localScrollback
        )
    }

    // MARK: - claimsPan

    func testClaimsVerticallyDominantVelocityEvenWithZeroTranslation() {
        XCTAssertTrue(TerminalWheelScroll.claimsPan(
            velocity: CGPoint(x: 30, y: -200), translation: .zero
        ))
    }

    func testRefusesHorizontallyDominantVelocity() {
        XCTAssertFalse(TerminalWheelScroll.claimsPan(
            velocity: CGPoint(x: 200, y: 30), translation: .zero
        ))
    }

    func testExactDiagonalTieGoesToVertical() {
        XCTAssertTrue(
            TerminalWheelScroll.claimsPan(velocity: CGPoint(x: 100, y: 100), translation: .zero),
            "the horizontal rule claims on a strict dx > dy, so ties must land here or no one takes the touch"
        )
    }

    func testFallsBackToTranslationOnlyWhileVelocityIsZero() {
        XCTAssertTrue(TerminalWheelScroll.claimsPan(velocity: .zero, translation: CGPoint(x: 4, y: 12)))
        XCTAssertFalse(TerminalWheelScroll.claimsPan(velocity: .zero, translation: CGPoint(x: 12, y: 4)))
    }

    func testStationaryTouchClaimsNothing() {
        XCTAssertFalse(TerminalWheelScroll.claimsPan(velocity: .zero, translation: .zero))
    }

    @MainActor
    func testClaimIsTheExactComplementOfTheHorizontalClaim() {
        // Both rules run on the same touch from different views; a drift between
        // them either double-claims a drag or leaves it unowned.
        let samples: [(velocity: CGPoint, translation: CGPoint)] = [
            (CGPoint(x: 200, y: 30), .zero),
            (CGPoint(x: 30, y: -200), .zero),
            (CGPoint(x: 100, y: 100), .zero),
            (CGPoint(x: -100, y: 100), .zero),
            (CGPoint(x: -8, y: -8), .zero),
            (CGPoint(x: 0, y: 5), .zero),
            (CGPoint(x: 5, y: 0), .zero),
            (.zero, CGPoint(x: 12, y: 4)),
            (.zero, CGPoint(x: 4, y: 12)),
            (.zero, CGPoint(x: -6, y: 6)),
        ]
        for sample in samples {
            let vertical = TerminalWheelScroll.claimsPan(
                velocity: sample.velocity, translation: sample.translation
            )
            let horizontal = TerminalPanScrollView.shouldClaimHorizontalPan(
                velocity: sample.velocity, translation: sample.translation, isDecelerating: false
            )
            XCTAssertNotEqual(
                vertical, horizontal,
                "exactly one axis must own v=\(sample.velocity) t=\(sample.translation)"
            )
        }
    }

    // MARK: - Emitted bytes

    func testWheelFlagsAreTheXtermWheelButtons() {
        XCTAssertEqual(TerminalWheelScroll.wheelFlags(down: false), 64)
        XCTAssertEqual(TerminalWheelScroll.wheelFlags(down: true), 65)
    }

    func testCursorKeyBytesFollowApplicationCursorMode() {
        XCTAssertEqual(
            TerminalWheelScroll.cursorKeyBytes(down: false, applicationCursor: false), [0x1B, 0x5B, 0x41]
        )
        XCTAssertEqual(
            TerminalWheelScroll.cursorKeyBytes(down: true, applicationCursor: false), [0x1B, 0x5B, 0x42]
        )
        XCTAssertEqual(
            TerminalWheelScroll.cursorKeyBytes(down: false, applicationCursor: true), [0x1B, 0x4F, 0x41],
            "DECCKM swaps CSI for SS3 — a full-screen pager reading raw arrows sees nothing otherwise"
        )
        XCTAssertEqual(
            TerminalWheelScroll.cursorKeyBytes(down: true, applicationCursor: true), [0x1B, 0x4F, 0x42]
        )
    }

    // MARK: - cell

    func testCellFloorsPointIntoAZeroBasedGridCell() {
        let cell = TerminalWheelScroll.cell(
            at: CGPoint(x: 95, y: 61),
            contentOffset: .zero,
            cellSize: CGSize(width: 10, height: 20),
            cols: 80,
            rows: 24
        )
        XCTAssertEqual(cell.col, 9, "sendEvent adds the protocol's own +1")
        XCTAssertEqual(cell.row, 3)
    }

    func testCellIsTakenRelativeToTheScrolledContentOffset() {
        let cell = TerminalWheelScroll.cell(
            at: CGPoint(x: 95, y: 61),
            contentOffset: CGPoint(x: 50, y: 40),
            cellSize: CGSize(width: 10, height: 20),
            cols: 80,
            rows: 24
        )
        XCTAssertEqual(cell.col, 4)
        XCTAssertEqual(cell.row, 1)
    }

    func testCellClampsIntoTheGrid() {
        let cell = TerminalWheelScroll.cell(
            at: CGPoint(x: 10_000, y: 10_000),
            contentOffset: .zero,
            cellSize: CGSize(width: 10, height: 20),
            cols: 80,
            rows: 24
        )
        XCTAssertEqual(cell.col, 79)
        XCTAssertEqual(cell.row, 23)
    }

    func testCellClampsToTheEncodableCoordinateCeiling() {
        // `Terminal.sendEvent` builds `UInt8(32 + coord + 1)` before clamping it,
        // so an X10-encoded coordinate of 223 traps — and Static mode allows 240
        // columns.
        let cell = TerminalWheelScroll.cell(
            at: CGPoint(x: 10_000, y: 10_000),
            contentOffset: .zero,
            cellSize: CGSize(width: 10, height: 20),
            cols: 240,
            rows: 300
        )
        XCTAssertEqual(cell.col, 222)
        XCTAssertEqual(cell.row, 222)
        XCTAssertLessThanOrEqual(32 + cell.col + 1, 255)
    }

    func testCellClampsPointsAboveAndLeftOfTheGridToTheOrigin() {
        let cell = TerminalWheelScroll.cell(
            at: CGPoint(x: -50, y: -50),
            contentOffset: .zero,
            cellSize: CGSize(width: 10, height: 20),
            cols: 80,
            rows: 24
        )
        XCTAssertEqual(cell.col, 0)
        XCTAssertEqual(cell.row, 0)
    }

    func testCellFallsBackToTheOriginBeforeCellMetricsExist() {
        let cell = TerminalWheelScroll.cell(
            at: CGPoint(x: 95, y: 61),
            contentOffset: .zero,
            cellSize: .zero,
            cols: 80,
            rows: 24
        )
        XCTAssertEqual(cell.col, 0)
        XCTAssertEqual(cell.row, 0)
    }

    // MARK: - TerminalWheelAccumulator

    func testSubRowDragsAccumulateAcrossCallbacksInsteadOfBeingLost() {
        var accumulator = TerminalWheelAccumulator()

        XCTAssertEqual(accumulator.rowsDown(panDeltaY: -12, cellHeight: 20), 0)
        XCTAssertEqual(
            accumulator.rowsDown(panDeltaY: -12, cellHeight: 20), 1,
            "24px of travel across two callbacks is one row — dropping the remainder makes a slow drag scroll nothing"
        )
    }

    func testDraggingDownScrollsTowardOlderContent() {
        var accumulator = TerminalWheelAccumulator()

        XCTAssertEqual(
            accumulator.rowsDown(panDeltaY: 40, cellHeight: 20), -2,
            "pulling the finger down pulls older content in, which is wheel-up"
        )
    }

    func testOppositeDragsCancelOut() {
        var accumulator = TerminalWheelAccumulator()

        let down = accumulator.rowsDown(panDeltaY: 30, cellHeight: 20)
        let up = accumulator.rowsDown(panDeltaY: -30, cellHeight: 20)

        XCTAssertEqual(down + up, 0, "a drag returned to its origin must leave the view where it started")
    }

    func testResetDropsTheCarriedRemainder() {
        var accumulator = TerminalWheelAccumulator()
        XCTAssertEqual(accumulator.rowsDown(panDeltaY: -12, cellHeight: 20), 0)

        accumulator.reset()

        XCTAssertEqual(
            accumulator.rowsDown(panDeltaY: -12, cellHeight: 20), 0,
            "a new gesture must not inherit the previous one's leftover pixels"
        )
    }

    func testUnmeasuredCellHeightYieldsNoRows() {
        var accumulator = TerminalWheelAccumulator()

        XCTAssertEqual(accumulator.rowsDown(panDeltaY: -400, cellHeight: 0), 0)
    }
}
