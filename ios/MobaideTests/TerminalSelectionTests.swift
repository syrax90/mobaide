import CoreGraphics
import SwiftTerm
import XCTest
@testable import Mobaide

final class TerminalSelectionTests: XCTestCase {
    private func cells(_ text: String) -> [TerminalSelection.Cell] {
        text.map { TerminalSelection.Cell(character: $0, width: 1) }
    }

    private func range(_ startCol: Int, _ startRow: Int, _ endCol: Int, _ endRow: Int) -> TerminalSelectionRange {
        TerminalSelectionRange(
            anchor: Position(col: startCol, row: startRow),
            focus: Position(col: endCol, row: endRow)
        )
    }

    // MARK: - Word expansion

    func testExpandsWordFromItsMiddle() {
        let row = cells("cd /usr/local bin")
        let word = TerminalSelection.expandWord(cells: row, at: 5)

        XCTAssertEqual(word, 3...12, "the whitespace-delimited run around the touched cell")
    }

    func testExpandsAtRowEdges() {
        let row = cells("abc def")

        XCTAssertEqual(TerminalSelection.expandWord(cells: row, at: 0), 0...2)
        XCTAssertEqual(TerminalSelection.expandWord(cells: row, at: 6), 4...6)
    }

    func testWideGlyphTrailingHalfStaysWithItsWord() {
        // "字" occupies two columns: the second carries no character of its own.
        let row = [
            TerminalSelection.Cell(character: "a", width: 1),
            TerminalSelection.Cell(character: "字", width: 2),
            TerminalSelection.Cell(character: " ", width: 0),
            TerminalSelection.Cell(character: "b", width: 1),
            TerminalSelection.Cell(character: " ", width: 1),
        ]

        XCTAssertEqual(
            TerminalSelection.expandWord(cells: row, at: 0),
            0...3,
            "a width-0 cell is the tail of the glyph before it, never a word break"
        )
    }

    func testSnapsColumnOutOfAWideGlyphsTrailingHalf() {
        let row = [
            TerminalSelection.Cell(character: "字", width: 2),
            TerminalSelection.Cell(character: " ", width: 0),
            TerminalSelection.Cell(character: "b", width: 1),
        ]

        XCTAssertEqual(TerminalSelection.snapToCellStart(1, cells: row), 0)
        XCTAssertEqual(TerminalSelection.snapToCellStart(2, cells: row), 2)
    }

    /// The two endpoints snap in opposite directions (Android `getValidCurX`):
    /// a start handle to the glyph's leading cell, an end handle to its
    /// trailing one. Snapping both backwards draws the highlight over half a
    /// character and puts the handle through the middle of it.
    func testEndEndpointSnapsForwardThroughAWideGlyph() {
        let row = [
            TerminalSelection.Cell(character: "字", width: 2),
            TerminalSelection.Cell(character: " ", width: 0),
            TerminalSelection.Cell(character: "b", width: 1),
        ]

        XCTAssertEqual(TerminalSelection.snapToCellEnd(0, cells: row), 1)
        XCTAssertEqual(TerminalSelection.snapToCellEnd(1, cells: row), 1)
        XCTAssertEqual(
            TerminalSelection.snapToCellEnd(2, cells: row),
            2,
            "a single-width cell is already its own end"
        )
    }

    // MARK: - Ordering and constraint

    func testStartAndEndAreOrderedRegardlessOfDragDirection() {
        let forward = range(2, 1, 7, 3)
        let backward = range(7, 3, 2, 1)

        XCTAssertEqual(forward.start, Position(col: 2, row: 1))
        XCTAssertEqual(forward.end, Position(col: 7, row: 3))
        XCTAssertEqual(backward.start, forward.start)
        XCTAssertEqual(backward.end, forward.end)
    }

    func testDraggedHandleIsPinnedRatherThanCrossingTheOther() {
        // End handle dragged above the anchor.
        let crossed = TerminalSelectionRange(
            anchor: Position(col: 5, row: 4),
            focus: Position(col: 1, row: 2)
        )

        let pinned = TerminalSelection.constrain(crossed, moving: .end)

        XCTAssertEqual(pinned.start, Position(col: 5, row: 4))
        XCTAssertEqual(pinned.end, Position(col: 5, row: 4), "the handles must never swap under the finger")
    }

    // MARK: - Highlight geometry

    private let cell = CGSize(width: 10, height: 20)

    func testSingleRowSelectionIsOneRectAndIncludesItsEndColumn() {
        let rects = TerminalSelection.highlightRects(
            start: Position(col: 2, row: 1),
            end: Position(col: 4, row: 1),
            cols: 80,
            cellSize: cell
        )

        XCTAssertEqual(rects, [CGRect(x: 20, y: 20, width: 30, height: 20)])
    }

    func testTwoRowSelectionHasNoEmptyMiddleBlock() {
        let rects = TerminalSelection.highlightRects(
            start: Position(col: 78, row: 1),
            end: Position(col: 1, row: 2),
            cols: 80,
            cellSize: cell
        )

        XCTAssertEqual(rects.count, 2)
        XCTAssertEqual(rects[0], CGRect(x: 780, y: 20, width: 20, height: 20))
        XCTAssertEqual(rects[1], CGRect(x: 0, y: 40, width: 20, height: 20))
    }

    func testMultiRowSelectionCollapsesTheMiddleIntoOneFullWidthBlock() {
        let rects = TerminalSelection.highlightRects(
            start: Position(col: 5, row: 1),
            end: Position(col: 2, row: 5),
            cols: 80,
            cellSize: cell
        )

        XCTAssertEqual(rects.count, 3)
        XCTAssertEqual(rects[0], CGRect(x: 50, y: 20, width: 750, height: 20))
        XCTAssertEqual(
            rects[1],
            CGRect(x: 0, y: 40, width: 800, height: 60),
            "rows 2-4 are one rect, so a selection spanning scrollback stays three shapes"
        )
        XCTAssertEqual(rects[2], CGRect(x: 0, y: 100, width: 30, height: 20))
    }

    func testCaretRectFollowsOrderedEdgesWhenTheDragRanBackwards() {
        let backwards = range(2, 3, 5, 1)

        XCTAssertEqual(
            TerminalSelection.caretRect(for: .start, range: backwards, cellSize: cell),
            CGRect(x: 49, y: 20, width: 2, height: 20),
            "start means the earlier endpoint, not the anchor"
        )
        XCTAssertEqual(
            TerminalSelection.caretRect(for: .end, range: backwards, cellSize: cell),
            CGRect(x: 29, y: 60, width: 2, height: 20)
        )
    }

    func testGrabbedHandlePicksTheNearerOneAndIgnoresDistantTouches() {
        let start = CGPoint(x: 20, y: 40)
        let end = CGPoint(x: 200, y: 40)

        XCTAssertEqual(TerminalSelection.grabbedHandle(at: CGPoint(x: 24, y: 44), start: start, end: end), .start)
        XCTAssertEqual(TerminalSelection.grabbedHandle(at: CGPoint(x: 196, y: 44), start: start, end: end), .end)
        XCTAssertNil(
            TerminalSelection.grabbedHandle(at: CGPoint(x: 110, y: 40), start: start, end: end),
            "a drag that starts on neither handle must fall through to the dismiss tap"
        )
    }

    // MARK: - Autoscroll

    func testAutoscrollTriggersOnlyNearTheViewportEdges() {
        let height: CGFloat = 600
        let cellHeight: CGFloat = 20

        XCTAssertEqual(TerminalSelection.autoscrollRows(viewportY: 5, viewportHeight: height, cellHeight: cellHeight), -1)
        XCTAssertEqual(TerminalSelection.autoscrollRows(viewportY: 595, viewportHeight: height, cellHeight: cellHeight), 1)
        XCTAssertEqual(TerminalSelection.autoscrollRows(viewportY: 300, viewportHeight: height, cellHeight: cellHeight), 0)
    }

    func testAutoscrollColumnBandSpansTwoCellsForWideFonts() {
        let width: CGFloat = 800
        let cellWidth: CGFloat = 20

        XCTAssertEqual(
            TerminalSelection.autoscrollColumns(viewportX: 35, viewportWidth: width, cellWidth: cellWidth),
            -1,
            "the horizontal band is two cells wide, wider than the 24pt floor here"
        )
        XCTAssertEqual(TerminalSelection.autoscrollColumns(viewportX: 765, viewportWidth: width, cellWidth: cellWidth), 1)
        XCTAssertEqual(TerminalSelection.autoscrollColumns(viewportX: 45, viewportWidth: width, cellWidth: cellWidth), 0)
    }

    // MARK: - Streaming output

    func testTrimShiftIgnoresRegionScrolls() {
        XCTAssertEqual(
            TerminalSelection.trimShift(
                scrollTop: 3,
                isAlternateBuffer: false,
                previousLinesTrimmed: 5,
                currentLinesTrimmed: 5
            ),
            0,
            "a DECSTBM region scroll shifts rows in place; indices are stable"
        )
    }

    func testTrimShiftRenumbersOncePerRecycledLine() {
        XCTAssertEqual(
            TerminalSelection.trimShift(
                scrollTop: 0,
                isAlternateBuffer: false,
                previousLinesTrimmed: 5,
                currentLinesTrimmed: 6
            ),
            -1,
            "the trim counter moving is what renumbers rows, and it counts only the recycling branch"
        )
    }

    /// The counter cannot answer for the alternate buffer — it keeps no
    /// scrollback, so SwiftTerm never records a trim there — but its ring is
    /// full from the first line, so every push renumbers every surviving row.
    /// Reading the counter alone would freeze the selection over a Claude Code
    /// or Codex repaint, which is the case the feature exists for.
    func testTrimShiftRenumbersEveryAlternateBufferScroll() {
        XCTAssertEqual(
            TerminalSelection.trimShift(
                scrollTop: 0,
                isAlternateBuffer: true,
                previousLinesTrimmed: 0,
                currentLinesTrimmed: 0
            ),
            -1,
            "the alternate buffer recycles on every scroll while never counting one"
        )
    }

    /// A frozen `yDisp` used to stand in for "a line was recycled". SwiftTerm
    /// also freezes it for as long as `Terminal.userScrolling` is set, which its
    /// iOS scroll view now does on any tracked drag — so on a ring with room to
    /// spare, a reader scrolled into history would have been told a line was
    /// trimmed on every streamed row, walking the highlight off its text.
    func testTrimShiftIsZeroWhileTheReaderIsScrolledBackOnARoomyRing() {
        XCTAssertEqual(
            TerminalSelection.trimShift(
                scrollTop: 0,
                isAlternateBuffer: false,
                previousLinesTrimmed: 0,
                currentLinesTrimmed: 0
            ),
            0,
            "a paused viewport is not a trim, however long yDisp sits still"
        )
    }

    func testScrollRegionIntersectionIsDetectedOnlyWhenItOverlaps() {
        let selection = range(0, 12, 10, 14)

        XCTAssertTrue(
            TerminalSelection.scrollRegionIntersects(selection, yDisp: 10, scrollTop: 0, scrollBottom: 5),
            "rows 10-15 overlap the selection at 12-14"
        )
        XCTAssertFalse(
            TerminalSelection.scrollRegionIntersects(selection, yDisp: 10, scrollTop: 5, scrollBottom: 9),
            "rows 15-19 sit entirely below the selection"
        )
        XCTAssertFalse(
            TerminalSelection.scrollRegionIntersects(selection, yDisp: 0, scrollTop: 0, scrollBottom: 5),
            "rows 0-5 sit entirely above the selection"
        )
    }
}
