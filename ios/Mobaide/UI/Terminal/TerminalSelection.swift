import CoreGraphics
import SwiftTerm

/// Which end of the selection a drag is moving.
enum TerminalSelectionHandle {
    case start
    case end
}

/// A selection as the user dragged it.
///
/// Rows are **content-absolute** — the same space as
/// `MobaideTerminalView.resolveRow`'s `contentRow`, the iOS renderer's row
/// index, and `Terminal.getText`'s `Position.row`. Viewport-relative rows would
/// have to be re-derived on every scroll, and `yDisp`-relative rows name
/// different lines once the user scrolls back — which they now do, since
/// SwiftTerm's iOS scroll view sets `Terminal.userScrolling` on a tracked drag
/// and parks `yDisp` below `yBase` for its duration.
///
/// `end.col` is **inclusive**, matching Android's `mSelX2`. The conversion to
/// `getText`'s exclusive end column happens at that one call site.
struct TerminalSelectionRange: Equatable {
    var anchor: Position
    var focus: Position

    init(anchor: Position, focus: Position) {
        self.anchor = anchor
        self.focus = focus
    }

    var start: Position {
        Position.compare(anchor, focus) == .after ? focus : anchor
    }

    var end: Position {
        Position.compare(anchor, focus) == .after ? anchor : focus
    }

    var isMultiLine: Bool { start.row != end.row }

    /// Both endpoints moved by `rows`, used when a scrollback trim renumbers
    /// every content row.
    func shifted(rows: Int) -> TerminalSelectionRange {
        TerminalSelectionRange(
            anchor: Position(col: anchor.col, row: anchor.row + rows),
            focus: Position(col: focus.col, row: focus.row + rows)
        )
    }
}

enum TerminalSelection {
    /// One terminal column. `width == 0` marks the trailing half of a
    /// double-width cell, which carries no character of its own.
    struct Cell: Equatable {
        let character: Character
        let width: Int

        init(character: Character, width: Int) {
            self.character = character
            self.width = width
        }
    }

    /// Radius within which a touch grabs a selection handle.
    static let handleGrabRadius: CGFloat = 22
    /// Drawn radius of a selection handle.
    static let handleRadius: CGFloat = 7

    /// The word around `col`, whitespace-delimited within this row only.
    ///
    /// Mirrors Android `TextSelectionCursorController.setInitialTextSelectionPosition`,
    /// which expands while the neighbouring cell yields non-empty trimmed text —
    /// i.e. while it is neither a space nor NUL. Never crosses rows: Android
    /// does not, and in a box-drawn TUI (Claude Code, Codex) crossing would
    /// swallow the border glyphs.
    static func expandWord(cells: [Cell], at col: Int) -> ClosedRange<Int> {
        guard !cells.isEmpty else { return 0...0 }
        let target = min(max(0, col), cells.count - 1)

        func isWord(_ index: Int) -> Bool {
            guard cells.indices.contains(index) else { return false }
            let cell = cells[index]
            // The trailing half of a wide glyph belongs to the glyph before it.
            if cell.width == 0 { return true }
            return cell.character != " " && cell.character != "\0"
        }

        // A blank cell selects only itself, so a long-press on padding does not
        // grab the whole run of spaces.
        guard isWord(target) else { return target...target }

        var lower = target
        var upper = target
        while lower > 0, isWord(lower - 1) { lower -= 1 }
        while upper < cells.count - 1, isWord(upper + 1) { upper += 1 }
        return lower...upper
    }

    /// Snaps a column that landed on the trailing half of a double-width glyph
    /// back to that glyph's first column.
    static func snapToCellStart(_ col: Int, cells: [Cell]) -> Int {
        guard !cells.isEmpty else { return max(0, col) }
        var index = min(max(0, col), cells.count - 1)
        while index > 0, cells[index].width == 0 { index -= 1 }
        return index
    }

    /// Snaps a column landing inside a double-width glyph *forward* to that
    /// glyph's last column.
    ///
    /// The direction depends on which endpoint is moving, which is what Android
    /// `getValidCurX` encodes: a start handle must sit on the glyph's leading
    /// cell, an end handle on its trailing one, or the highlight covers half a
    /// character and the handle is drawn through the middle of it. Extraction is
    /// unaffected either way — `getText` takes an exclusive end column — so this
    /// is purely what the user sees.
    static func snapToCellEnd(_ col: Int, cells: [Cell]) -> Int {
        guard !cells.isEmpty else { return max(0, col) }
        var index = min(max(0, col), cells.count - 1)
        while index + 1 < cells.count, cells[index + 1].width == 0 { index += 1 }
        return index
    }

    static func clamp(_ position: Position, cols: Int, maxRow: Int) -> Position {
        Position(
            col: min(max(0, position.col), max(0, cols - 1)),
            row: min(max(0, position.row), max(0, maxRow))
        )
    }

    /// Keeps the dragged handle from crossing the fixed one (Android
    /// `updatePosition` pins rather than flipping, so the handles never swap
    /// under the finger).
    static func constrain(
        _ range: TerminalSelectionRange,
        moving handle: TerminalSelectionHandle
    ) -> TerminalSelectionRange {
        switch handle {
        case .end:
            guard Position.compare(range.focus, range.anchor) == .before else { return range }
            return TerminalSelectionRange(anchor: range.anchor, focus: range.anchor)
        case .start:
            guard Position.compare(range.focus, range.anchor) == .after else { return range }
            return TerminalSelectionRange(anchor: range.anchor, focus: range.anchor)
        }
    }

    /// Highlight geometry in **content** coordinates: at most three rects —
    /// the first row's remainder, one block for the full-width middle rows, and
    /// the last row's prefix.
    static func highlightRects(
        start: Position,
        end: Position,
        cols: Int,
        cellSize: CGSize
    ) -> [CGRect] {
        guard cols > 0, cellSize.width > 0, cellSize.height > 0 else { return [] }
        guard Position.compare(start, end) != .after else { return [] }

        func rect(row: Int, fromCol: Int, throughCol: Int, rows: Int = 1) -> CGRect? {
            let lower = min(max(0, fromCol), cols - 1)
            let upper = min(max(0, throughCol), cols - 1)
            guard upper >= lower else { return nil }
            return CGRect(
                x: CGFloat(lower) * cellSize.width,
                y: CGFloat(row) * cellSize.height,
                width: CGFloat(upper - lower + 1) * cellSize.width,
                height: CGFloat(rows) * cellSize.height
            )
        }

        if start.row == end.row {
            return [rect(row: start.row, fromCol: start.col, throughCol: end.col)].compactMap { $0 }
        }

        var rects: [CGRect] = []
        if let first = rect(row: start.row, fromCol: start.col, throughCol: cols - 1) {
            rects.append(first)
        }
        let middleRows = end.row - start.row - 1
        if middleRows > 0,
           let middle = rect(row: start.row + 1, fromCol: 0, throughCol: cols - 1, rows: middleRows) {
            rects.append(middle)
        }
        if let last = rect(row: end.row, fromCol: 0, throughCol: end.col) {
            rects.append(last)
        }
        return rects
    }

    /// Handle centres in content coordinates, hung off the bottom of their row.
    /// The end handle sits at the column *after* the last selected one, so it
    /// brackets the selection rather than covering its final glyph.
    static func handleCenters(
        start: Position,
        end: Position,
        cellSize: CGSize
    ) -> (start: CGPoint, end: CGPoint) {
        (
            start: CGPoint(
                x: CGFloat(start.col) * cellSize.width,
                y: CGFloat(start.row + 1) * cellSize.height
            ),
            end: CGPoint(
                x: CGFloat(end.col + 1) * cellSize.width,
                y: CGFloat(end.row + 1) * cellSize.height
            )
        )
    }

    /// A caret-shaped rect at `handle`'s edge of the selection, spanning its
    /// row, in content coordinates — what the magnifier loupe tracks while that
    /// handle is dragged.
    static func caretRect(
        for handle: TerminalSelectionHandle,
        range: TerminalSelectionRange,
        cellSize: CGSize
    ) -> CGRect {
        let centers = handleCenters(start: range.start, end: range.end, cellSize: cellSize)
        let bottom = handle == .start ? centers.start : centers.end
        let width: CGFloat = 2
        return CGRect(
            x: bottom.x - width / 2,
            y: bottom.y - cellSize.height,
            width: width,
            height: cellSize.height
        )
    }

    /// The handle a touch grabs, or nil when it landed on neither. Ties go to
    /// the end handle, the one a fresh drag most often means to extend.
    static func grabbedHandle(
        at point: CGPoint,
        start: CGPoint,
        end: CGPoint,
        radius: CGFloat = handleGrabRadius
    ) -> TerminalSelectionHandle? {
        func distance(_ a: CGPoint, _ b: CGPoint) -> CGFloat {
            let dx = a.x - b.x
            let dy = a.y - b.y
            return (dx * dx + dy * dy).squareRoot()
        }
        let toStart = distance(point, start)
        let toEnd = distance(point, end)
        guard min(toStart, toEnd) <= radius else { return nil }
        return toStart < toEnd ? .start : .end
    }

    /// The narrowest edge band, so a finger held still at the edge stays inside
    /// it even with a small font.
    static let autoscrollMinBand: CGFloat = 24

    /// Rows to scroll per autoscroll tick while a handle is held near a
    /// viewport edge: -1 up, +1 down, 0 otherwise. `viewportY` is the touch's y
    /// in **viewport** coordinates.
    static func autoscrollRows(
        viewportY: CGFloat,
        viewportHeight: CGFloat,
        cellHeight: CGFloat
    ) -> Int {
        guard cellHeight > 0 else { return 0 }
        return autoscrollStep(at: viewportY, extent: viewportHeight, band: cellHeight)
    }

    /// Columns to scroll per autoscroll tick while a handle is held near the
    /// left (-1) or right (+1) edge of a Static-mode viewport, 0 otherwise.
    /// `viewportX` is the touch's x in **viewport** coordinates.
    static func autoscrollColumns(
        viewportX: CGFloat,
        viewportWidth: CGFloat,
        cellWidth: CGFloat
    ) -> Int {
        guard cellWidth > 0 else { return 0 }
        return autoscrollStep(at: viewportX, extent: viewportWidth, band: cellWidth * 2)
    }

    private static func autoscrollStep(at position: CGFloat, extent: CGFloat, band: CGFloat) -> Int {
        guard extent > 0 else { return 0 }
        // A band rather than a strict out-of-bounds test, so the user does not
        // have to drag off the view to extend. Capped at a third of the
        // viewport so the two bands cannot overlap on a short one.
        let width = min(max(band, autoscrollMinBand), extent / 3)
        if position < width { return -1 }
        if position > extent - width { return 1 }
        return 0
    }

    /// How far content rows renumber after an emulator scroll.
    ///
    /// Which arms of `Terminal.scroll` reach the line list at all is
    /// `TerminalFollowScroll.pushesRowIntoScrollback`'s rule — it owns the
    /// caveats, including the narrow-margin (DECLRMM) arm that also runs with
    /// `scrollTop == 0` and which neither predicate can distinguish from
    /// outside the emulator, so it reports a shift that did not happen.
    ///
    /// What is specific here: of the scrolls that *do* push, only those that
    /// recycle a line renumber anything. While the ring has room `yBase`
    /// advances and existing indices hold; once it is full the oldest line is
    /// recycled and every surviving row shifts down by one.
    ///
    /// The count comes from `Buffer.totalLinesTrimmed` rather than from `yDisp`
    /// standing still. A frozen `yDisp` is not evidence of a trim: SwiftTerm
    /// also freezes it for the whole time `Terminal.userScrolling` is set, which
    /// its iOS scroll view now does on any tracked drag, so on a ring with room
    /// to spare a reader scrolled into history would otherwise be told a line
    /// was trimmed on every streamed row. `totalLinesTrimmed` increments on the
    /// recycling branch alone.
    ///
    /// The alternate buffer is the one case that counter cannot answer: it keeps
    /// no scrollback, so SwiftTerm never counts a trim there — but its ring is
    /// full from the first line, so every push renumbers every surviving row.
    static func trimShift(
        scrollTop: Int,
        isAlternateBuffer: Bool,
        previousLinesTrimmed: Int,
        currentLinesTrimmed: Int
    ) -> Int {
        guard TerminalFollowScroll.pushesRowIntoScrollback(scrollTop: scrollTop) else { return 0 }
        if isAlternateBuffer { return -1 }
        // A hard reset restarts the count; treat that as no shift rather than
        // shifting the selection the wrong way on the negative delta.
        return -max(0, currentLinesTrimmed - previousLinesTrimmed)
    }

    /// Whether an emulator scroll region overlaps the selection.
    ///
    /// A region scroll overwrites its own rows in place, so the selected text is
    /// gone rather than moved — the selection must be cancelled, not shifted.
    /// `scrollTop`/`scrollBottom` are screen-relative; the selection is
    /// content-absolute, hence the offset.
    ///
    /// The offset that is strictly correct is `yBase`, the origin of the region
    /// the emulator writes to, rather than `yDisp`, the origin of what the
    /// reader is looking at. They are equal except while a drag holds
    /// `Terminal.userScrolling`, which parks `yDisp` below `yBase` — and `yBase`
    /// is `internal`, with no public quantity standing in for it (`yDisp` and
    /// `totalLinesTrimmed` are the only two published, and neither tracks it).
    /// So a region scroll arriving while the reader is scrolled back is tested
    /// against the band they are looking at instead of the band being written.
    /// The alternate buffer, where both are pinned to 0, is unaffected — which
    /// is the arm that carries every Claude Code and Codex repaint.
    static func scrollRegionIntersects(
        _ range: TerminalSelectionRange,
        yDisp: Int,
        scrollTop: Int,
        scrollBottom: Int
    ) -> Bool {
        let regionTop = yDisp + scrollTop
        let regionBottom = yDisp + scrollBottom
        return range.start.row <= regionBottom && range.end.row >= regionTop
    }
}
