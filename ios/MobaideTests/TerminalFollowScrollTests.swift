import CoreGraphics
import XCTest
@testable import Mobaide

/// Follow-mode scroll math (Android `onScrollPositionChanged` /
/// `isAutoScrollDisabled` parity): where the viewport sits in the scrollback,
/// and which programmatic offset writes are SwiftTerm's snap-to-tail.
final class TerminalFollowScrollTests: XCTestCase {
    // MARK: - maxOffsetY

    func testMaxOffsetIsTheScrollableRange() {
        XCTAssertEqual(
            TerminalFollowScroll.maxOffsetY(contentHeight: 1500, viewportHeight: 600), 900
        )
    }

    func testContentShorterThanTheViewportHasNoScrollableRange() {
        XCTAssertEqual(
            TerminalFollowScroll.maxOffsetY(contentHeight: 300, viewportHeight: 600), 0,
            "a fresh session's buffer is exactly one screen — a negative range would invert every position"
        )
    }

    // MARK: - position

    func testTailReadsAsFullyScrolled() {
        XCTAssertEqual(TerminalFollowScroll.position(offsetY: 900, maxOffsetY: 900), 1)
    }

    func testTopOfTheScrollbackReadsAsZero() {
        XCTAssertEqual(TerminalFollowScroll.position(offsetY: 0, maxOffsetY: 900), 0)
    }

    func testMidwayReadsAsTheFraction() {
        XCTAssertEqual(TerminalFollowScroll.position(offsetY: 450, maxOffsetY: 900), 0.5, accuracy: 1e-9)
    }

    func testContentThatFitsTheViewportAlwaysReadsAsFollowing() {
        XCTAssertEqual(
            TerminalFollowScroll.position(offsetY: 0, maxOffsetY: 0), 1,
            "there is no scrollback to leave, so the viewer cannot have fallen behind"
        )
    }

    func testRubberBandOverscrollClampsIntoTheUnitRange() {
        // UIScrollView drives the offset past both ends during a bounce, and
        // SwiftTerm's own snap lands a partial row below the tail.
        XCTAssertEqual(TerminalFollowScroll.position(offsetY: -80, maxOffsetY: 900), 0)
        XCTAssertEqual(TerminalFollowScroll.position(offsetY: 980, maxOffsetY: 900), 1)
    }

    // MARK: - isAtBottom

    func testThresholdIsInclusive() {
        XCTAssertTrue(TerminalFollowScroll.isAtBottom(position: TerminalFollowScroll.followThreshold))
        XCTAssertFalse(TerminalFollowScroll.isAtBottom(position: 0.998))
    }

    func testOneRowOffTheTailPausesFollowOnAPageOfScrollback() {
        // The epsilon has to absorb sub-point content-size rounding without
        // swallowing a real drag: ~350 rows of history, dragged up by one.
        let position = TerminalFollowScroll.position(offsetY: 900 - 17, maxOffsetY: 900)

        XCTAssertFalse(
            TerminalFollowScroll.isAtBottom(position: position),
            "a one-row drag is the smallest gesture that must reach the jump-to-bottom button"
        )
    }

    // MARK: - holdsOffset

    /// SwiftTerm's `updateScroller()`/`ensureCaretIsVisible()` write
    /// `(lines.count - rows) * cellHeight`, which sits a partial row *below*
    /// `maxOffsetY` because the grid row count floors the viewport height.
    func testHoldsSwiftTermsSnapToTheTailFromAScrolledUpViewport() {
        XCTAssertTrue(TerminalFollowScroll.holdsOffset(incomingY: 905, currentY: 300, maxOffsetY: 900))
    }

    func testHoldsASnapThatLandsExactlyOnTheTail() {
        XCTAssertTrue(TerminalFollowScroll.holdsOffset(incomingY: 900, currentY: 300, maxOffsetY: 900))
    }

    func testSubPointRoundingStillCountsAsTheTail() {
        XCTAssertTrue(
            TerminalFollowScroll.holdsOffset(incomingY: 899.75, currentY: 300, maxOffsetY: 900),
            "content height is cellHeight * lineCount, so the snap can land fractionally short"
        )
    }

    func testDoesNotHoldWhenTheViewportIsAlreadyAtTheTail() {
        XCTAssertFalse(
            TerminalFollowScroll.holdsOffset(incomingY: 905, currentY: 900, maxOffsetY: 900),
            "a following viewport has nothing to protect — refusing here would freeze normal output"
        )
    }

    func testDoesNotHoldAnOffsetWriteThatIsNotTheTail() {
        XCTAssertFalse(
            TerminalFollowScroll.holdsOffset(incomingY: 400, currentY: 300, maxOffsetY: 900),
            "rotation and contentSize clamping must pass through untouched"
        )
    }

    func testDoesNotHoldWhenThereIsNoScrollback() {
        XCTAssertFalse(
            TerminalFollowScroll.holdsOffset(incomingY: 0, currentY: 0, maxOffsetY: 0),
            "with the whole buffer on screen every offset is the tail, so holding would pin a stale origin"
        )
    }
}
