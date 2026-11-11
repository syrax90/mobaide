import UIKit
import XCTest
@testable import Mobaide

/// `TerminalPanScrollView.revealCursorIfArmed()` against a real
/// `MobaideTerminalView` in a 390×600 phone viewport (Android
/// `TerminalHorizontalScrollViewRevealCursorTest` parity). Bytes go in
/// through `feedTrackingSynchronizedOutput`, the only production path to the
/// emulator, and the reveal is invoked the way the Coordinator's paint
/// callbacks invoke it.
@MainActor
final class TerminalPanScrollViewCursorRevealTests: XCTestCase {
    private let viewport = CGRect(x: 0, y: 0, width: 390, height: 600)

    private func makeViews(
        mode: TerminalDisplayMode,
        staticColumns: Int = 120
    ) -> (scroll: TerminalPanScrollView, terminal: MobaideTerminalView) {
        let terminal = MobaideTerminalView(frame: .zero)
        terminal.configureAppearance()
        let scroll = TerminalPanScrollView(
            terminalView: terminal,
            displayConfig: TerminalDisplayConfig(mode: mode, staticColumns: staticColumns)
        )
        scroll.frame = viewport
        scroll.layoutIfNeeded()
        // Second pass with a settled cellSize (see TerminalPanScrollViewTests).
        scroll.setNeedsLayout()
        scroll.layoutIfNeeded()
        return (scroll, terminal)
    }

    private func staticViews() -> (scroll: TerminalPanScrollView, terminal: MobaideTerminalView) {
        let views = makeViews(mode: .static)
        XCTAssertGreaterThan(views.scroll.contentSize.width, viewport.width, "precondition: 120 columns overflow the phone")
        XCTAssertEqual(views.terminal.getTerminal().getDims().cols, 120)
        return views
    }

    private func feed(_ terminal: MobaideTerminalView, _ text: String) {
        terminal.feedTrackingSynchronizedOutput(Array(text.utf8)[...])
    }

    private func cursorLeft(_ terminal: MobaideTerminalView) -> CGFloat {
        CGFloat(terminal.getTerminal().getCursorLocation().x) * terminal.cellSize.width
    }

    private func assertCursorOnScreen(
        _ scroll: TerminalPanScrollView,
        _ terminal: MobaideTerminalView,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let left = cursorLeft(terminal)
        let right = left + terminal.cellSize.width
        let offset = scroll.contentOffset.x
        XCTAssertTrue(
            left >= offset && right <= offset + scroll.bounds.width,
            "cursor cell [\(left), \(right)] must sit inside the visible window [\(offset), \(offset + scroll.bounds.width)]",
            file: file,
            line: line
        )
    }

    // MARK: - Reveal

    func testTypingPastTheRightEdgePansSoTheCursorSitsAQuarterViewportInside() {
        let (scroll, terminal) = staticViews()
        let widthBefore = terminal.frame.width
        let contentBefore = scroll.contentSize
        terminal.armCursorReveal()

        feed(terminal, String(repeating: "x", count: 60))
        scroll.revealCursorIfArmed()

        XCTAssertGreaterThan(cursorLeft(terminal), viewport.width, "col 60 starts beyond a 390 pt viewport")
        assertCursorOnScreen(scroll, terminal)
        XCTAssertEqual(
            cursorLeft(terminal) + terminal.cellSize.width - scroll.contentOffset.x,
            viewport.width * 3 / 4,
            accuracy: 1,
            "lookahead: the cursor's right edge lands at 3/4 of the viewport, not flush with the edge"
        )
        XCTAssertEqual(terminal.frame.width, widthBefore, "only the offset moves — a width change would resize the PTY")
        XCTAssertEqual(scroll.contentSize, contentBefore)
        XCTAssertEqual(scroll.contentOffset.y, 0, "the reveal is horizontal only")
    }

    func testReturningToTheBurstOriginAfterAPanPansBack() {
        let (scroll, terminal) = staticViews()
        terminal.armCursorReveal()
        feed(terminal, String(repeating: "x", count: 60))
        scroll.revealCursorIfArmed()
        XCTAssertGreaterThan(scroll.contentOffset.x, 0, "precondition: the first reveal panned right")

        feed(terminal, "\r")
        XCTAssertEqual(terminal.getTerminal().getCursorLocation().x, 0, "precondition: the cursor is back at the origin cell")
        scroll.revealCursorIfArmed()

        XCTAssertEqual(
            scroll.contentOffset.x, 0,
            "Home / Ctrl-U to the origin must not strand the view at the old column"
        )
    }

    func testFingerOnTheTerminalHoldsTheRevealUntilLift() {
        let (scroll, terminal) = staticViews()
        terminal.armCursorReveal()
        feed(terminal, String(repeating: "x", count: 60))

        terminal.touchesBegan(Set<UITouch>(), with: nil)
        XCTAssertTrue(terminal.isTouchActive)
        scroll.revealCursorIfArmed()
        XCTAssertEqual(scroll.contentOffset.x, 0, "a pan under a scrollback or wheel drag would fight the gesture")

        terminal.touchesEnded(Set<UITouch>(), with: nil)
        XCTAssertFalse(terminal.isTouchActive)
        scroll.revealCursorIfArmed()
        assertCursorOnScreen(scroll, terminal)
    }

    func testCancelledTouchReleasesTheHold() {
        let (scroll, terminal) = staticViews()
        terminal.armCursorReveal()
        feed(terminal, String(repeating: "x", count: 60))

        terminal.touchesBegan(Set<UITouch>(), with: nil)
        terminal.touchesCancelled(Set<UITouch>(), with: nil)
        scroll.revealCursorIfArmed()

        assertCursorOnScreen(scroll, terminal)
    }

    func testRunningFlingHoldsTheReveal() {
        let (scroll, terminal) = staticViews()
        // Mouse reporting routes the fling through the app's own wheel ladder,
        // the arm that owns a `flingTask`.
        feed(terminal, "\u{1B}[?1000h\u{1B}[?1006h")
        terminal.armCursorReveal()
        feed(terminal, String(repeating: "x", count: 60))

        terminal.startFling(velocity: CGPoint(x: 0, y: -20_000))
        XCTAssertNotNil(terminal.flingTask, "precondition: the fling is running")
        scroll.revealCursorIfArmed()
        XCTAssertEqual(scroll.contentOffset.x, 0, "momentum scrolling must not be yanked sideways")

        terminal.cancelFling()
        scroll.revealCursorIfArmed()
        assertCursorOnScreen(scroll, terminal)
    }

    func testFlingThatRunsOutOnItsOwnReleasesTheHold() async {
        let (scroll, terminal) = staticViews()
        feed(terminal, "\u{1B}[?1000h\u{1B}[?1006h")
        terminal.armCursorReveal()
        feed(terminal, String(repeating: "x", count: 60))

        terminal.startFling(velocity: CGPoint(x: 0, y: -20_000))
        XCTAssertNotNil(terminal.flingTask, "precondition: the fling is running")
        await terminal.flingTask?.value

        XCTAssertNil(terminal.flingTask, "a finished fling must not read as still flinging")
        scroll.revealCursorIfArmed()
        assertCursorOnScreen(scroll, terminal)
    }

    func testSupersededFlingDoesNotClearItsReplacement() async {
        let (_, terminal) = staticViews()
        feed(terminal, "\u{1B}[?1000h\u{1B}[?1006h")

        terminal.startFling(velocity: CGPoint(x: 0, y: -20_000))
        let superseded = terminal.flingTask
        XCTAssertNotNil(superseded, "precondition: the first fling is running")
        terminal.startFling(velocity: CGPoint(x: 0, y: -20_000))
        let replacement = terminal.flingTask
        XCTAssertNotNil(replacement, "precondition: the second fling is running")

        await superseded?.value

        XCTAssertEqual(
            terminal.flingTask, replacement,
            "the cancelled fling's exit must not wipe the newer fling's handle"
        )
        terminal.cancelFling()
    }

    // MARK: - Coordinator callbacks

    private var retainedModel: TerminalSessionModel?

    private func coordinator(hosting scroll: TerminalPanScrollView) -> TerminalHostView.Coordinator {
        let model = TerminalTestSupport.model()
        retainedModel = model
        let coordinator = TerminalHostView.Coordinator(model: model)
        coordinator.panHost = scroll
        return coordinator
    }

    func testEmulatorScrolledCallbackNeverReveals() {
        let (scroll, terminal) = staticViews()
        let coordinator = coordinator(hosting: scroll)
        terminal.armCursorReveal()
        feed(terminal, String(repeating: "x", count: 60))

        coordinator.scrolled(source: terminal, position: 0)

        XCTAssertEqual(
            scroll.contentOffset.x, 0,
            "`scrolled` fires mid-parse, with the cursor partway through the frame"
        )
    }

    func testRangeChangedCallbackReveals() {
        let (scroll, terminal) = staticViews()
        let coordinator = coordinator(hosting: scroll)
        terminal.armCursorReveal()
        feed(terminal, String(repeating: "x", count: 60))

        coordinator.rangeChanged(source: terminal, startY: 0, endY: 0)

        assertCursorOnScreen(scroll, terminal)
    }

    func testOutputWithoutAKeystrokeNeverPans() {
        let (scroll, terminal) = staticViews()

        feed(terminal, String(repeating: "x", count: 60))
        scroll.revealCursorIfArmed()

        XCTAssertEqual(scroll.contentOffset.x, 0, "passive output must not move the user's horizontal position")
    }

    func testSynchronizedOutputFreezeHoldsTheReveal() {
        let (scroll, terminal) = staticViews()
        terminal.armCursorReveal()

        feed(terminal, "\u{1B}[?2026h" + String(repeating: "x", count: 60))
        scroll.revealCursorIfArmed()

        XCTAssertEqual(
            scroll.contentOffset.x, 0,
            "the glass still shows the pre-freeze frame — panning to a cursor the user cannot see yet would jump early"
        )

        feed(terminal, "\u{1B}[?2026l")
        scroll.revealCursorIfArmed()

        assertCursorOnScreen(scroll, terminal)
    }

    func testRevealSkipsAfterTheTerminalIsReparented() {
        let (scroll, terminal) = staticViews()
        terminal.armCursorReveal()
        feed(terminal, String(repeating: "x", count: 60))
        let thief = UIView(frame: viewport)
        thief.addSubview(terminal)

        scroll.revealCursorIfArmed()

        XCTAssertEqual(scroll.contentOffset.x, 0, "a stale wrapper must not pan for a view it no longer hosts")
    }

    // MARK: - Arming at the terminal view

    func testInsertTextArmsTheLatch() {
        let (_, terminal) = staticViews()

        terminal.insertText("a")

        XCTAssertTrue(terminal.cursorReveal.isArmed, "a soft-keyboard character is the user typing")
    }
}
