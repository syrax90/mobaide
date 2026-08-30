import UIKit
import XCTest
@testable import Mobaide

/// Unit tests for `TerminalGridEstimate`, the grid the create request names
/// before any terminal exists to measure.
///
/// The agent spawns the PTY at that grid and the program lays its opening
/// frames out there. Those bytes then sit in the agent's replay ring carrying
/// no width of their own, so a wrong column count is not self-correcting —
/// every later reconnect re-renders them at the real grid.
final class TerminalGridEstimateTests: XCTestCase {

    private func config(_ mode: TerminalDisplayMode, columns: Int = 120) -> TerminalDisplayConfig {
        TerminalDisplayConfig(mode: mode, staticColumns: columns)
    }

    func testStaticColumnsIgnoreTheViewport() {
        // Static draws a surface wider than the screen and pans it, so the count
        // is pinned and owes nothing to geometry. A viewport that would divide
        // down to 40 columns must still report the configured 120.
        let narrow = TerminalGridEstimate.columns(
            config: config(.static), viewportWidth: 320, cellWidth: 8
        )
        let wide = TerminalGridEstimate.columns(
            config: config(.static), viewportWidth: 1366, cellWidth: 8
        )

        XCTAssertEqual(narrow, 120)
        XCTAssertEqual(wide, 120, "the same session must not re-grid on rotation in Static")
    }

    func testFullColumnsDivideTheViewportByTheCell() {
        let columns = TerminalGridEstimate.columns(
            config: config(.full), viewportWidth: 390, cellWidth: 7.8
        )

        XCTAssertEqual(columns, 50)
    }

    func testFullColumnsFallBackToStaticColumnsWhenNothingCanBeMeasured() {
        // An unmeasurable cell or a viewport with no extent must not divide down
        // to zero: the configured Static count is the sane stand-in.
        XCTAssertEqual(
            TerminalGridEstimate.columns(config: config(.full), viewportWidth: 390, cellWidth: 0),
            120
        )
        XCTAssertEqual(
            TerminalGridEstimate.columns(config: config(.full), viewportWidth: 0, cellWidth: 7.8),
            120
        )
    }

    func testFullColumnsFloorAtOne() {
        // A viewport narrower than a single cell still has a positive width, so
        // it clears the guard above and reaches the division. A zero-column PTY
        // is not a grid any program can lay out in.
        let columns = TerminalGridEstimate.columns(
            config: config(.full), viewportWidth: 5, cellWidth: 7.8
        )

        XCTAssertEqual(columns, 1)
    }

    func testRowsDivideTheViewportByTheCellHeight() {
        XCTAssertEqual(TerminalGridEstimate.rows(viewportHeight: 844, cellHeight: 15.5), 54)
    }

    func testRowsFallBackToTheDefaultWhenNothingCanBeMeasured() {
        XCTAssertEqual(TerminalGridEstimate.rows(viewportHeight: 844, cellHeight: 0), 24)
        XCTAssertEqual(TerminalGridEstimate.rows(viewportHeight: 0, cellHeight: 15.5), 24)
    }

    func testRowsFloorAtOne() {
        XCTAssertEqual(TerminalGridEstimate.rows(viewportHeight: 5, cellHeight: 15.5), 1)
    }

    func testCellSizeSnapsToTheDevicePixelGrid() {
        // SwiftTerm rounds its cell up to a whole device pixel before laying the
        // grid out. The estimate has to snap identically, or a pre-layout column
        // count drifts from the one the view later derives from the same font —
        // which is the drift this helper exists to prevent.
        for scale in [CGFloat(2), CGFloat(3)] {
            let cell = TerminalGridEstimate.cellSize(scale: scale)

            XCTAssertEqual(
                (cell.width * scale).rounded(), cell.width * scale, accuracy: 0.0001,
                "width must land on a whole device pixel at scale \(scale)"
            )
            XCTAssertEqual(
                (cell.height * scale).rounded(), cell.height * scale, accuracy: 0.0001,
                "height must land on a whole device pixel at scale \(scale)"
            )
        }
    }

    func testCellSizeNeverCollapsesBelowOnePoint() {
        // A degenerate face would otherwise produce a sub-point cell, and the
        // viewport division above would then claim thousands of columns.
        let cell = TerminalGridEstimate.cellSize(
            font: .monospacedSystemFont(ofSize: 0.1, weight: .regular), scale: 2
        )

        XCTAssertGreaterThanOrEqual(cell.width, 1)
        XCTAssertGreaterThanOrEqual(cell.height, 1)
    }

    func testCellSizeGrowsWithThePointSize() {
        // Pins that the measurement is of the font it was handed rather than a
        // constant — the parity claim against the terminal view rests on it.
        let small = TerminalGridEstimate.cellSize(
            font: .monospacedSystemFont(ofSize: 13, weight: .regular), scale: 2
        )
        let large = TerminalGridEstimate.cellSize(
            font: .monospacedSystemFont(ofSize: 26, weight: .regular), scale: 2
        )

        XCTAssertGreaterThan(large.width, small.width)
        XCTAssertGreaterThan(large.height, small.height)
    }
}
