import CoreGraphics

/// Scroll math for terminal follow mode: where the user's viewport sits, and
/// which emulator scrolls actually moved the tail away from it.
///
/// SwiftTerm's iOS renderer picks the first visible row straight out of
/// `contentOffset` (`AppleTerminalView.drawTerminalContents`), but its
/// `scrollPosition` and `getLine(row:)` read `buffer.yDisp`, which tracks a drag
/// only partially: `iOSTerminalView.syncYDispFromContentOffset` sets
/// `Terminal.userScrolling` and moves `yDisp` while `isTracking`, then stops at
/// the row the finger lifted on, so momentum and every programmatic write leave
/// it behind the viewport. The scroll view is therefore still the only honest
/// source for "how far up is the user", which makes every viewport rule here a
/// function of `contentOffset` / `contentSize` / bounds.
/// `pushesRowIntoScrollback` is the one exception — it answers from emulator
/// state, because only the emulator knows whether a row was produced.
///
/// The corollary that matters elsewhere: `yDisp` standing still no longer means
/// the scrollback ring filled, because `userScrolling` freezes it too. Anything
/// counting trimmed rows reads `Buffer.totalLinesTrimmed` instead — see
/// `TerminalSelection.trimShift`.
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
    /// `ensureCaretIsVisible()` (every byte sent) drive `contentOffset` toward
    /// the tail, and both are `internal` — unreachable for an override. They are
    /// recognizable by what they write: exactly the bottom, from somewhere above
    /// it. Anything else — UIKit clamping after a `contentSize` shrink, a
    /// rotation, a deliberate jump-to-bottom — is left alone.
    ///
    /// `updateScroller()` is no longer an unconditional slam: it now writes a
    /// `yDisp`-derived offset, which lands short of the bottom whenever
    /// `userScrolling` has parked `yDisp` above the tail, and this predicate
    /// answers `false` for those. That is the safe direction for a paused
    /// viewport, but it is why a fling that coasts past the finger-lift row can
    /// still be pulled back — see the manual smoke list rather than a test,
    /// since no unit test can produce live UIKit tracking.
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
    /// moving the moment the ring fills, so a delta would silently stop counting
    /// a few hundred lines into any real session. (`Buffer.totalLinesTrimmed`
    /// does keep going and is public, but it counts only the recycling branch,
    /// which is a different question from "did this scroll produce a row".)
    ///
    /// The narrow-margin arm is deliberately not detected, so DECLRMM scrolls
    /// still count. Nothing about that arm is legible from outside the package:
    /// `marginMode`, which SwiftTerm ands into its own test, is `internal`, and
    /// neither margin value stands in for it. `Buffer.resize` clamps both
    /// margins **downward only**, so any grid that narrows and widens again —
    /// a rotation, the keyboard appearing — strands them below the full width
    /// for the rest of the session, which would make a `marginRight < cols - 1`
    /// term true forever and collapse SwiftTerm's own test to bare `marginMode`.
    /// `marginLeft` is worse: `ESC[?69l` leaves it raised, and the only write
    /// that could lower it clamps toward that stranded `marginRight`, so a
    /// program that sets a left margin and then drops DECLRMM would pin the
    /// badge at zero for the rest of the session — twelve bytes from the PTY
    /// silently disabling the indicator.
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
