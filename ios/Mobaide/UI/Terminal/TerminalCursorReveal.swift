import CoreGraphics
import Foundation

/// Static-mode horizontal cursor follow (Android `TerminalCursorReveal`):
/// while the user types, `TerminalPanScrollView` pans so the cursor column
/// stays on screen.
///
/// Only keyboard-originated input arms the latch — never emulator auto-replies,
/// mouse clicks or wheel reports (a vertical drag in a mouse-tracking TUI is a
/// stream of wheel reports, and arming on it would yank the view sideways). The
/// keystroke's echo arrives a network round trip later, so every painted update
/// inside `window` of the last keystroke may reveal. Until the cursor has left
/// the burst's origin it never reveals: TUIs that park a hidden cursor at a
/// fixed spot would otherwise drag the view there on every key. Once it has
/// moved, a return to the origin (Home, Ctrl-U) is followed too.
@MainActor
final class TerminalCursorReveal {
    static let window: TimeInterval = 2

    /// Cells kept visible beyond the cursor before a pan is triggered.
    private static let edgeMarginCells: CGFloat = 2

    private let now: () -> TimeInterval
    private var deadline: TimeInterval = 0
    private var origin = (col: 0, row: 0)
    private var moved = false

    init(now: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }) {
        self.now = now
    }

    var isArmed: Bool { now() < deadline }

    /// Opens (or extends) the reveal window; the origin is kept across a burst of keys.
    func arm(col: Int, row: Int) {
        if !isArmed {
            origin = (col, row)
            moved = false
        }
        deadline = now() + Self.window
    }

    func disarm() {
        deadline = 0
    }

    func shouldReveal(col: Int, row: Int) -> Bool {
        guard isArmed else { return false }
        if col != origin.col || row != origin.row { moved = true }
        return moved
    }

    /// The content offset that brings the cursor cell into view, or nil when it
    /// is already visible (or there is nothing to scroll). Crossing an edge jumps
    /// a quarter viewport past it, so typing pans in chunks rather than one cell
    /// per keystroke.
    static func targetOffsetX(
        cursorX: CGFloat,
        cellWidth: CGFloat,
        viewportWidth: CGFloat,
        contentWidth: CGFloat,
        currentOffsetX: CGFloat
    ) -> CGFloat? {
        let maxOffset = contentWidth - viewportWidth
        guard maxOffset > 0, cellWidth > 0 else { return nil }
        let margin = edgeMarginCells * cellWidth
        let inset = viewportWidth / 4
        let cursorRight = cursorX + cellWidth
        let target: CGFloat
        if cursorRight > currentOffsetX + viewportWidth - margin {
            target = cursorRight - (viewportWidth - inset)
        } else if currentOffsetX > 0, cursorX < currentOffsetX + margin {
            target = cursorX - inset
        } else {
            return nil
        }
        let clamped = min(max(target.rounded(), 0), maxOffset)
        return clamped == currentOffsetX ? nil : clamped
    }
}
