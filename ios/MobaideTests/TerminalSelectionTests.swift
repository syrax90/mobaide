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

    func testExpandsFromWordStartAndEndAlike() {
        let row = cells("cd /usr/local bin")

        XCTAssertEqual(TerminalSelection.expandWord(cells: row, at: 3), 3...12)
        XCTAssertEqual(TerminalSelection.expandWord(cells: row, at: 12), 3...12)
    }

    func testBlankCellSelectsOnlyItself() {
        let row = cells("cd  ls")

        XCTAssertEqual(
            TerminalSelection.expandWord(cells: row, at: 2),
            2...2,
            "a long-press on padding must not grab the whole run of spaces"
        )
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

    func testConstrainLeavesAValidDragAlone() {
        let valid = TerminalSelectionRange(
            anchor: Position(col: 1, row: 2),
            focus: Position(col: 5, row: 4)
        )

        XCTAssertEqual(TerminalSelection.constrain(valid, moving: .end), valid)
    }

    func testClampKeepsPositionsInsideTheBuffer() {
        let clamped = TerminalSelection.clamp(Position(col: 999, row: 999), cols: 80, maxRow: 40)

        XCTAssertEqual(clamped, Position(col: 79, row: 40))
        XCTAssertEqual(
            TerminalSelection.clamp(Position(col: -3, row: -3), cols: 80, maxRow: 40),
            Position(col: 0, row: 0)
        )
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

    /// The toolbar anchors on the union of these rects. Across rows `end.col`
    /// is usually left of `start.col`, so anything derived from
    /// `end.col - start.col` goes negative and collapses to a sliver at the
    /// right edge — dragging from mid-line down onto a shorter line is the
    /// ordinary case for copying a stack trace.
    func testMultiRowRectsUnionToTheFullHighlightBox() {
        let rects = TerminalSelection.highlightRects(
            start: Position(col: 40, row: 1),
            end: Position(col: 3, row: 4),
            cols: 80,
            cellSize: cell
        )

        let union = rects.reduce(CGRect.null) { $0.union($1) }
        XCTAssertEqual(union, CGRect(x: 0, y: 20, width: 800, height: 80))
        XCTAssertGreaterThan(union.width, cell.width, "the anchor must not collapse to a single cell")
    }

    func testHandleCentersBracketTheSelection() {
        let centers = TerminalSelection.handleCenters(
            start: Position(col: 2, row: 1),
            end: Position(col: 4, row: 1),
            cellSize: cell
        )

        XCTAssertEqual(centers.start, CGPoint(x: 20, y: 40))
        XCTAssertEqual(
            centers.end,
            CGPoint(x: 50, y: 40),
            "the end handle sits after the last selected column so it does not cover its glyph"
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

    func testAutoscrollBandsCannotOverlapOnAShortViewport() {
        // Two full-cell bands would cover the whole viewport and pin it to -1.
        let rows = TerminalSelection.autoscrollRows(viewportY: 15, viewportHeight: 30, cellHeight: 20)

        XCTAssertEqual(rows, 0, "the band is capped at a third of the viewport so the middle stays neutral")
    }

    // MARK: - Streaming output

    func testTrimShiftIgnoresRegionScrolls() {
        XCTAssertEqual(
            TerminalSelection.trimShift(scrollTop: 3, previousYDisp: 5, currentYDisp: 5),
            0,
            "a DECSTBM region scroll shifts rows in place; indices are stable"
        )
    }

    func testTrimShiftIsZeroWhileTheRingStillHasRoom() {
        XCTAssertEqual(
            TerminalSelection.trimShift(scrollTop: 0, previousYDisp: 5, currentYDisp: 6),
            0,
            "yDisp advancing means a line was appended, not recycled"
        )
    }

    func testTrimShiftRenumbersOnceTheRingIsFull() {
        XCTAssertEqual(
            TerminalSelection.trimShift(scrollTop: 0, previousYDisp: 5, currentYDisp: 5),
            -1,
            "yDisp frozen while a line was pushed means the oldest line was recycled"
        )
    }

    func testShiftMovesBothEndpoints() {
        let shifted = range(2, 10, 5, 12).shifted(rows: -1)

        XCTAssertEqual(shifted.start, Position(col: 2, row: 9))
        XCTAssertEqual(shifted.end, Position(col: 5, row: 11))
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
