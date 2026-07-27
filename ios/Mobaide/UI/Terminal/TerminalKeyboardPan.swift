import CoreGraphics

/// Pan-don't-resize keyboard math (Android `ImeFloatingPan` /
/// `imeCursorCounterPan`). The terminal grid keeps its size while the keyboard
/// is up: the screen lifts the whole column by `lift`, and the wrapper
/// counter-pans the terminal down by `counterPan` so the net movement is
/// exactly enough to keep the caret anchor row visible — zero when the caret
/// sits high on screen, the full lift when it sits at the grid bottom. All
/// values are in window points, y-down.
enum TerminalKeyboardPan {
    /// Rows kept visible below the caret (Android `CURSOR_ANCHOR_SLACK_ROWS`).
    static let cursorAnchorSlackRows = 2

    /// UIKit's private keyboard curve resolves to this spring. The SwiftUI
    /// column lift and the UIKit counter-pan animator must both build from
    /// these values — if they drift apart the two pans stop cancelling when
    /// the net movement should be zero.
    static let springMass: CGFloat = 3
    static let springStiffness: CGFloat = 1000
    static let springDamping: CGFloat = 500

    /// Keyboard overlap above the home-indicator line (Android
    /// `(ime.bottom - systemBars.bottom).coerceAtLeast(0)`). Returns 0 for
    /// undocked/floating/split keyboards (frame not touching the window bottom
    /// or narrower than it) and for the hardware-keyboard case, where the
    /// residual overlap is near zero.
    static func lift(keyboardEndFrame: CGRect, windowBounds: CGRect, bottomSafeInset: CGFloat) -> CGFloat {
        guard keyboardEndFrame.maxY >= windowBounds.maxY - 1 else { return 0 }
        guard keyboardEndFrame.width >= windowBounds.width - 1 else { return 0 }
        return max(0, windowBounds.maxY - keyboardEndFrame.minY - bottomSafeInset)
    }

    /// Caret bottom plus slack rows, in terminal-viewport coordinates.
    /// `caretFrame` is in SwiftTerm content coordinates (it tracks the cursor
    /// even while the TUI hides the caret); subtracting the scroll offset
    /// converts. `.greatestFiniteMagnitude` when the caret view is not yet
    /// realized — Android's `Int.MAX_VALUE` default, meaning no counter-pan.
    static func caretAnchorBottom(caretFrame: CGRect, contentOffsetY: CGFloat, cellHeight: CGFloat) -> CGFloat {
        guard caretFrame.height >= 1 else { return .greatestFiniteMagnitude }
        return caretFrame.maxY - contentOffsetY + CGFloat(cursorAnchorSlackRows) * cellHeight
    }

    /// Downward compensation applied to the terminal's frame origin (Android
    /// `dy = (pan == 0) ? 0 : (height - anchor).coerceIn(0, pan)`).
    static func counterPan(boxHeight: CGFloat, caretAnchorBottom: CGFloat, lift: CGFloat) -> CGFloat {
        guard lift > 0 else { return 0 }
        return min(max(boxHeight - caretAnchorBottom, 0), lift)
    }
}
