import CoreGraphics

/// Scroll-position math for terminal follow mode, derived from the scroll view
/// rather than from the emulator.
///
/// SwiftTerm's iOS renderer picks the first visible row straight out of
/// `contentOffset` (`AppleTerminalView.drawTerminalContents`), but its
/// `scrollPosition` and `getLine(row:)` read `buffer.yDisp` — and nothing in the
/// package maps a drag onto `yDisp` (`Terminal.userScrolling` is never written
/// outside SwiftTerm's own tests, so `yDisp` stays pinned to `yBase`). The scroll
/// view is therefore the only honest source for "how far up is the user", which
/// makes every rule here a function of `contentOffset` / `contentSize` / bounds.
enum TerminalFollowScroll {
    /// A position at or above this counts as following the tail. The two
    /// at-the-bottom constants work in different spaces and are not
    /// interchangeable: this one is the fraction-space test used by
    /// `isAtBottom(position:)` and `TerminalSessionModel.onUserScrolled`,
    /// `bottomTolerance` the points-space slack.
    static let followThreshold = 0.999

    /// Points of slack when comparing an offset against the bottom, covering the
    /// sub-point rounding of a `cellHeight * lineCount` content size. Used by
    /// `holdsOffset` and by the view's `scrollToBottom()` early-out.
    static let bottomTolerance: CGFloat = 0.5

    static func maxOffsetY(contentHeight: CGFloat, viewportHeight: CGFloat) -> CGFloat {
        max(0, contentHeight - viewportHeight)
    }

    /// - Returns: `0` at the top of the scrollback through `1` at the tail.
    ///   Content that fits the viewport has no scrollback to leave, so it always
    ///   reads as following; rubber-band overscroll is clamped rather than
    ///   reported as a position outside the range.
    static func position(offsetY: CGFloat, maxOffsetY: CGFloat) -> Double {
        guard maxOffsetY > 0, offsetY.isFinite else { return 1 }
        return Double(min(max(offsetY / maxOffsetY, 0), 1))
    }

    static func isAtBottom(position: Double) -> Bool {
        position >= followThreshold
    }

    /// Whether a programmatic offset write must be refused to keep a paused
    /// viewport still (Android `TerminalEmulator.isAutoScrollDisabled`).
    ///
    /// SwiftTerm's `updateScroller()` (every scrolled line) and
    /// `ensureCaretIsVisible()` (every byte sent) both slam `contentOffset` to
    /// the tail, and both are `internal` — unreachable for an override. They are
    /// recognizable by what they write: exactly the bottom, from somewhere above
    /// it. Anything else — UIKit clamping after a `contentSize` shrink, a
    /// rotation, a deliberate jump-to-bottom — is left alone.
    static func holdsOffset(incomingY: CGFloat, currentY: CGFloat, maxOffsetY: CGFloat) -> Bool {
        guard maxOffsetY > 0, incomingY.isFinite, currentY.isFinite else { return false }
        let bottom = maxOffsetY - bottomTolerance
        return incomingY >= bottom && currentY < bottom
    }
}
