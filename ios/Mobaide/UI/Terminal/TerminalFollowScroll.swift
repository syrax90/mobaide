import CoreGraphics

/// Scroll math for terminal follow mode: where the user's viewport sits, and
/// which emulator scrolls actually moved the tail away from it.
///
/// SwiftTerm's iOS renderer picks the first visible row straight out of
/// `contentOffset` (`AppleTerminalView.drawTerminalContents`), but its
/// `scrollPosition` and `getLine(row:)` read `buffer.yDisp` — and nothing in the
/// package maps a drag onto `yDisp` (`Terminal.userScrolling` is never written
/// outside SwiftTerm's own tests, so `yDisp` stays pinned to `yBase`). The scroll
/// view is therefore the only honest source for "how far up is the user", which
/// makes every viewport rule here a function of `contentOffset` / `contentSize`
/// / bounds. `pushesRowIntoScrollback` is the one exception — it answers from
/// emulator state, because only the emulator knows whether a row was produced.
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

    /// Whether the scroll `Terminal.scroll` just performed pushed a row into the
    /// scrollback — the only kind a paused reader actually missed, and so the
    /// only kind the unread badge may count.
    ///
    /// `Terminal.scroll` has three arms and reports all of them through the same
    /// `scrolled` callback. Two of them relocate rows inside the viewport and
    /// leave the tail exactly where it was: the narrow-margin arm (DECLRMM
    /// block-copies within the margin columns) and the `scrollTop != 0` arm (a
    /// DECSTBM region shifts in place, overwriting its own top row). Only the
    /// remaining arm reaches the line list, which is why this is its entry
    /// condition rather than anything read after the fact — `buffer.yDisp` stops
    /// moving the moment the ring fills (`linesTop`, the counter that keeps
    /// going, is `internal`), so a delta would silently stop counting a few
    /// hundred lines into any real session.
    ///
    /// The narrow-margin arm is deliberately not detected, so DECLRMM scrolls
    /// still count. Nothing about that arm is legible from outside the package:
    /// `marginMode`, which SwiftTerm ands into its own test, is `internal`, and
    /// neither margin value stands in for it. `Buffer.resize` clamps
    /// `marginRight` **downward only**, and a `UIViewRepresentable` is built
    /// before SwiftUI hands it a frame, so the two-column grid `Terminal.setup()`
    /// runs on strands `marginRight` at 1 for the session — which both makes a
    /// `marginRight < cols - 1` term true forever and collapses SwiftTerm's own
    /// test to bare `marginMode`. `marginLeft` is worse: `ESC[?69l` leaves it
    /// raised, and the only write that could lower it clamps toward that
    /// stranded `marginRight`, so a program that sets a left margin and then
    /// drops DECLRMM would pin the badge at zero for the rest of the session —
    /// twelve bytes from the PTY silently disabling the indicator.
    ///
    /// So the residual error is biased entirely toward over-counting, which is
    /// the direction that cannot hide output from the reader.
    ///
    /// Android applies the same rule from inside its emulator, where the arms
    /// are unambiguous: `TerminalEmulator.scrollDownOneLine` bumps
    /// `mScrollCounter` only on the branch that rotates the screen window, so it
    /// catches the narrow-margin case this cannot. It also deliberately reaches
    /// a different number for DECSTBM — there the region's top row is parked in
    /// the transcript (`TerminalBuffer.scrollDownOneLine` rotates
    /// `mScreenFirstRow` and grows `mActiveTranscriptRows`) where SwiftTerm
    /// overwrites it in place. The rule is in parity; the count is not.
    static func pushesRowIntoScrollback(scrollTop: Int) -> Bool {
        scrollTop == 0
    }
}
