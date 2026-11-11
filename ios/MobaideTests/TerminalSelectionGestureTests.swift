import UIKit
import XCTest
@testable import Mobaide

@MainActor
final class TerminalSelectionGestureTests: XCTestCase {
    private var retainedCoordinator: TerminalHostView.Coordinator?

    private func makeTerminal() -> MobaideTerminalView {
        let terminal = MobaideTerminalView(frame: CGRect(x: 0, y: 0, width: 390, height: 600))
        terminal.configureAppearance()
        terminal.layoutIfNeeded()
        return terminal
    }

    private func recognizer(named name: String, on view: UIView) -> UIGestureRecognizer? {
        (view.gestureRecognizers ?? []).first { $0.name == name }
    }

    /// A terminal with its Coordinator attached, so the emulator's `scrolled`
    /// callback actually reaches the selection controller — the path that keeps
    /// a highlight glued to its text while output streams.
    private func wiredTerminal() -> (TerminalSessionModel, MobaideTerminalView) {
        let model = TerminalTestSupport.model()
        let terminal = model.terminalView
        terminal.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        terminal.configureAppearance()
        terminal.layoutIfNeeded()
        let coordinator = TerminalHostView.Coordinator(model: model)
        retainedCoordinator = coordinator
        terminal.terminalDelegate = coordinator
        return (model, terminal)
    }

    /// Drives the emulator the way a real TUI does, so arbitration is read from
    /// live state rather than a flag the test sets.
    private func enterAlternateScreen(_ terminal: MobaideTerminalView) {
        terminal.feed(text: "\u{1B}[?1049h")
    }

    private func enableMouseReporting(_ terminal: MobaideTerminalView) {
        terminal.feed(text: "\u{1B}[?1000h\u{1B}[?1006h")
    }

    /// A point inside the first row, in content coordinates.
    private func point(row: Int, col: Int, on terminal: MobaideTerminalView) -> CGPoint {
        let cell = terminal.cellSize
        return CGPoint(
            x: CGFloat(col) * cell.width + cell.width / 2,
            y: CGFloat(row) * cell.height + cell.height / 2
        )
    }

    // MARK: - Neutralization must not touch the selection recognizers

    func testSelectionRecognizersSurviveNeutralizationAndMouseModeChanges() {
        let terminal = makeTerminal()

        terminal.neutralizeSwiftTermGestures()
        terminal.mouseModeChanged(source: terminal.getTerminal())

        for name in [
            TerminalSelectionController.longPressGestureName,
            TerminalSelectionController.dragGestureName,
            TerminalSelectionController.dismissTapGestureName,
        ] {
            let gesture = recognizer(named: name, on: terminal)
            XCTAssertNotNil(gesture, "\(name) must be installed")
            XCTAssertTrue(
                gesture?.isEnabled == true,
                "\(name) carries the mobaide. prefix, so the sweep — which re-runs on every mouse-mode flip — must spare it"
            )
        }
    }

    // MARK: - Sticky mode owns the pointer

    func testSelectionSuspendsEveryOtherTerminalGesture() throws {
        let terminal = makeTerminal()
        terminal.feed(text: "hello world\r\n")
        // The case the feature exists for: Claude Code / Codex run here.
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)
        XCTAssertEqual(terminal.wheelScrollMode, .wheel, "the harness must reproduce a mouse-reporting TUI")

        XCTAssertTrue(terminal.beginSelectionForTest(at: point(row: 0, col: 1, on: terminal)))

        let verticalScroll = try XCTUnwrap(
            recognizer(named: MobaideTerminalView.verticalScrollGestureName, on: terminal)
        )
        XCTAssertFalse(
            terminal.gestureRecognizerShouldBegin(verticalScroll),
            "the wheel ladder would otherwise turn a handle drag into scroll reports"
        )
        XCTAssertFalse(terminal.gestureRecognizerShouldBegin(terminal.panGestureRecognizer))

        for name in ["mobaide.rowTap", "mobaide.rowDoubleTap", "mobaide.mouseTap"] {
            let tap = UITapGestureRecognizer()
            tap.name = name
            terminal.addGestureRecognizer(tap)
            XCTAssertFalse(
                terminal.gestureRecognizerShouldBegin(tap),
                "\(name) must not fire while a selection is live"
            )
        }
    }

    func testGesturesAreRestoredWhenSelectionEnds() throws {
        let terminal = makeTerminal()
        terminal.feed(text: "hello world\r\n")
        terminal.beginSelectionForTest(at: point(row: 0, col: 1, on: terminal))

        terminal.selectionController.endSelection()

        let verticalScroll = try XCTUnwrap(
            recognizer(named: MobaideTerminalView.verticalScrollGestureName, on: terminal)
        )
        // Back to the normal ladder: no mouse reporting here, so the wheel pan
        // is refused on its own terms rather than by the selection gate.
        XCTAssertFalse(terminal.isSelectionActive)
        XCTAssertEqual(terminal.wheelScrollMode, .localScrollback)
        XCTAssertFalse(terminal.gestureRecognizerShouldBegin(verticalScroll))
        XCTAssertTrue(terminal.gestureRecognizerShouldBegin(terminal.panGestureRecognizer))
    }

    func testStaticModeHorizontalPanIsRefusedWhileSelecting() {
        let terminal = makeTerminal()
        let scroll = TerminalPanScrollView(
            terminalView: terminal,
            displayConfig: TerminalDisplayConfig(mode: .static, staticColumns: 120)
        )
        scroll.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        scroll.layoutIfNeeded()
        scroll.setNeedsLayout()
        scroll.layoutIfNeeded()
        terminal.feed(text: "hello world\r\n")

        terminal.beginSelectionForTest(at: point(row: 0, col: 1, on: terminal))

        XCTAssertFalse(
            scroll.gestureRecognizerShouldBegin(scroll.panGestureRecognizer),
            "the outer pan lives on another view, so it needs its own gate or it pans the terminal out from under the selection"
        )
    }

    func testSelectionHoldsTheViewportWithoutTouchingFollowMode() {
        let terminal = makeTerminal()
        terminal.feed(text: "hello world\r\n")
        XCTAssertFalse(terminal.holdsAutoScroll)

        terminal.beginSelectionForTest(at: point(row: 0, col: 1, on: terminal))
        XCTAssertTrue(terminal.holdsAutoScroll, "incoming output must not drag the highlight away mid-drag")
        XCTAssertFalse(terminal.isAutoScrollSuppressed, "follow mode is the model's to own")

        terminal.selectionController.endSelection()
        XCTAssertFalse(terminal.holdsAutoScroll, "the hold is self-restoring")
    }

    // MARK: - Synchronized output

    func testSelectionIsRefusedAndCancelledWhileOutputIsFrozen() {
        let terminal = makeTerminal()
        var now: TimeInterval = 0
        terminal.synchronizedOutputNow = { now }
        terminal.feed(text: "hello world\r\n")

        // Begin synchronized output (DEC mode 2026): the renderer now shows a
        // frozen snapshot while getText still reads the live buffer.
        terminal.feedTrackingSynchronizedOutput(Array("\u{1B}[?2026h".utf8)[...])
        XCTAssertTrue(terminal.isSynchronizedOutputActive)

        XCTAssertFalse(
            terminal.beginSelectionForTest(at: point(row: 0, col: 1, on: terminal)),
            "a selection started here would highlight one row and copy another"
        )
    }

    func testActiveSelectionIsCancelledWhenOutputFreezes() {
        let terminal = makeTerminal()
        var now: TimeInterval = 0
        terminal.synchronizedOutputNow = { now }
        terminal.feed(text: "hello world\r\n")
        XCTAssertTrue(terminal.beginSelectionForTest(at: point(row: 0, col: 1, on: terminal)))

        terminal.feedTrackingSynchronizedOutput(Array("\u{1B}[?2026h".utf8)[...])

        XCTAssertFalse(terminal.isSelectionActive)
    }

    // MARK: - Extraction

    func testSelectedTextIsTheWordUnderTheLongPress() {
        let terminal = makeTerminal()
        terminal.feed(text: "cd /usr/local/bin\r\n")

        terminal.beginSelectionForTest(at: point(row: 0, col: 5, on: terminal))

        XCTAssertEqual(terminal.selectionController.selectedText(), "/usr/local/bin")
    }

    func testSoftWrappedSelectionRejoinsWithoutANewline() throws {
        let terminal = makeTerminal()
        let cols = terminal.getTerminal().getDims().cols
        // A path longer than one row, so the emulator soft-wraps it. Dragging
        // the end handle onto the continuation row must copy it back seamlessly;
        // a newline in the middle is the failure this guards.
        let path = String(repeating: "a", count: cols + 12)
        terminal.feed(text: path)
        let controller = terminal.selectionController
        controller.beginSelection(at: point(row: 0, col: 0, on: terminal))

        controller.moveHandle(.end, to: point(row: 1, col: 11, on: terminal))

        let text = try XCTUnwrap(controller.selectedText())
        XCTAssertFalse(text.contains("\n"), "a soft-wrapped run must rejoin seamlessly")
        XCTAssertEqual(text, path)
    }

    func testHardLineBreakKeepsItsNewline() throws {
        let terminal = makeTerminal()
        terminal.feed(text: "alpha\r\nbeta\r\n")
        let controller = terminal.selectionController
        controller.beginSelection(at: point(row: 0, col: 0, on: terminal))

        controller.moveHandle(.end, to: point(row: 1, col: 3, on: terminal))

        XCTAssertEqual(
            try XCTUnwrap(controller.selectedText()),
            "alpha\nbeta",
            "a hard break is a real line ending and must survive the copy"
        )
    }

    // MARK: - Selections must not outlive the buffer they were taken in

    /// The whole point of the sticky mode is that selection works in Claude
    /// Code and Codex, which stream on the alternate buffer. Cancelling on
    /// every alt-buffer scroll would kill it on the first streamed line.
    func testSelectionSurvivesStreamingOnTheAlternateBuffer() throws {
        let (model, terminal) = wiredTerminal()
        enterAlternateScreen(terminal)
        let rows = terminal.getTerminal().getDims().rows
        // Fill the alt screen exactly, so the next lines really scroll rather
        // than just advancing the cursor.
        for line in 0..<rows { terminal.feed(text: "line\(line)\r\n") }
        // Select on the last row, which has room to travel upward.
        XCTAssertTrue(terminal.beginSelectionForTest(at: point(row: rows - 1, col: 1, on: terminal)))
        let before = try XCTUnwrap(terminal.selectionController.range)

        terminal.feed(text: "more\r\nmore\r\n")

        XCTAssertTrue(
            terminal.isSelectionActive,
            "streaming output must shift the highlight, not destroy it — this is the primary target case"
        )
        let after = try XCTUnwrap(terminal.selectionController.range)
        XCTAssertEqual(
            after.start.row,
            before.start.row - 2,
            "the alt buffer recycles rows with yDisp pinned at 0, so the selection follows its text upward"
        )
        withExtendedLifetime(model) {}
    }

    func testJumpToBottomStillWorksWithALiveSelection() throws {
        let (model, terminal) = wiredTerminal()
        for line in 0..<80 { terminal.feed(text: "line\(line)\r\n") }
        terminal.layoutIfNeeded()
        // Pause follow mode the way a user drag does, then select in history.
        model.onUserScrolled(position: 0.1)
        XCTAssertFalse(model.followMode)
        terminal.contentOffset = CGPoint(x: 0, y: 0)
        XCTAssertTrue(terminal.beginSelectionForTest(at: point(row: 5, col: 1, on: terminal)))

        model.jumpToBottom()

        XCTAssertTrue(
            terminal.isScrolledToBottom,
            "a live selection pins the viewport via holdsAutoScroll, so jump-to-bottom must drop it first or silently do nothing"
        )
        XCTAssertFalse(terminal.isSelectionActive)
    }

    func testSelectionIsCancelledWhenTheScreenBufferSwitches() {
        let terminal = makeTerminal()
        enterAlternateScreen(terminal)
        terminal.feed(text: "hello world\r\n")
        XCTAssertTrue(terminal.beginSelectionForTest(at: point(row: 0, col: 1, on: terminal)))

        // Leaving the alt screen renumbers every row: the alt buffer runs at
        // yDisp 0 over `rows` lines, the normal one can sit thousands deep.
        terminal.feedTrackingSynchronizedOutput(Array("\u{1B}[?1049l".utf8)[...])

        XCTAssertFalse(
            terminal.isSelectionActive,
            "a selection taken inside Claude Code or less must not survive the exit — Copy would lift unrelated text"
        )
    }

    func testSelectionIsCancelledByAReplayWipe() async throws {
        let factory = FakeWebSocketTransportFactory()
        let model = TerminalTestSupport.model(transportFactory: factory)
        model.terminalView.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        model.terminalView.configureAppearance()
        model.terminalView.layoutIfNeeded()
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        model.terminalView.feed(text: "hello world\r\n")
        XCTAssertTrue(model.terminalView.beginSelectionForTest(
            at: point(row: 0, col: 1, on: model.terminalView)
        ))

        // 'b' — replayStart, over the real wire path. The transcript those rows
        // name is about to be erased and refilled from the agent's ring buffer.
        connection.emit(.binary(Data([0x62])))

        let cleared = await poll { !model.terminalView.isSelectionActive }
        XCTAssertTrue(
            cleared,
            "a reconnect mid-selection must not leave coordinates pointing at replacement content"
        )
    }

    private func poll(_ condition: () -> Bool) async -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return condition()
    }

    func testEndingAVisibleSelectionRestoresTheTailWhenFollowing() {
        let (model, terminal) = wiredTerminal()
        for line in 0..<80 { terminal.feed(text: "line\(line)\r\n") }
        terminal.layoutIfNeeded()
        XCTAssertTrue(model.followMode)
        XCTAssertTrue(terminal.beginSelectionForTest(at: point(row: 2, col: 1, on: terminal)))
        // Output arriving while the selection pins the viewport.
        terminal.contentOffset = CGPoint(x: 0, y: 0)

        model.copySelection()

        XCTAssertTrue(
            terminal.isScrolledToBottom,
            "releasing the holdsAutoScroll pin does not itself move the viewport — following users would be stranded on a stale one with no unread badge and no jump-to-bottom button"
        )
    }

    func testEndingASelectionLeavesAPausedViewportAlone() {
        let (model, terminal) = wiredTerminal()
        for line in 0..<80 { terminal.feed(text: "line\(line)\r\n") }
        terminal.layoutIfNeeded()
        // The user scrolled up: follow mode is off, so the tail must NOT be restored.
        model.onUserScrolled(position: 0.1)
        XCTAssertFalse(model.followMode)
        terminal.contentOffset = CGPoint(x: 0, y: 0)
        XCTAssertTrue(terminal.beginSelectionForTest(at: point(row: 2, col: 1, on: terminal)))

        model.copySelection()

        XCTAssertFalse(
            terminal.isScrolledToBottom,
            "a paused viewport is the user's, and ending a selection must not yank it to the tail"
        )
    }

    func testLeavingTheScreenEndsTheSelection() {
        let (model, terminal) = wiredTerminal()
        terminal.feed(text: "hello world\r\n")
        XCTAssertTrue(terminal.beginSelectionForTest(at: point(row: 0, col: 1, on: terminal)))
        XCTAssertTrue(terminal.holdsAutoScroll)

        model.markHidden()

        XCTAssertFalse(terminal.isSelectionActive)
        XCTAssertFalse(
            terminal.holdsAutoScroll,
            "the retained view keeps consuming output while hidden — a selection left live would pin the viewport with no unread badge and no jump-to-bottom button to escape it"
        )
    }

    /// A scrollback wipe renumbers every absolute row, and `getText` clamps
    /// rather than trapping — so without a staleness guard Copy silently
    /// returns whatever now sits at those coordinates. Rows below `rows` are
    /// the hard case: `bufferLine` resolves them happily, because its window
    /// origin collapses to 0.
    func testCopyRefusesAfterAScrollbackWipeRenumbersTheRows() throws {
        let (model, terminal) = wiredTerminal()
        for line in 0..<100 { terminal.feed(text: "line\(line)\r\n") }
        // A low row, deep in the transcript — the case the row-existence guard
        // cannot catch on its own.
        XCTAssertTrue(terminal.beginSelectionForTest(at: point(row: 3, col: 1, on: terminal)))
        XCTAssertEqual(terminal.selectionController.selectedText(), "line3")

        terminal.feed(text: "\u{1B}[3J")

        XCTAssertNotEqual(
            terminal.selectionController.selectedText(),
            "line67",
            "row 3 now names entirely different content — Copy must not lift it"
        )
        XCTAssertNil(
            terminal.selectionController.selectedText(),
            "a selection whose transcript was trimmed underneath it copies nothing rather than the wrong thing"
        )
        withExtendedLifetime(model) {}
    }

    // MARK: - CLA-186 follow-up: yDisp parked short of the bottom by a fling

    /// Reproduces the defect a stale `yDisp`-derived bound caused. Verified
    /// against the pre-fix formula (`yDisp + rows - 1`) with this harness's own
    /// numbers: it silently clamped a touch on the actually-touched row
    /// ("line56") down to the stale bound and returned "line36" instead —
    /// exactly the "Copy returns some other row's text" bug. `position(at:)`
    /// now bounds the touch by `lastContentRow`, so it no longer clamps into
    /// range and silently substitutes a row nobody touched.
    ///
    /// That does not make the touched row selectable here: `bufferLine`'s own
    /// lookup window is bounded by the very same parked `yDisp` (a limit this
    /// diff does not touch, guarding a stale-`contentSize` scrollback-wipe case
    /// — see its doc comment), so a row this far outside it fails closed
    /// instead. The fix's guarantee is "no selection", never "the wrong one".
    func testSelectionBeyondPositionOnParkedYDispFailsClosedRatherThanRelocating() throws {
        let terminal = makeTerminal()
        let rows = terminal.getTerminal().getDims().rows
        let extra = 20
        for line in 0..<(rows + extra) {
            terminal.feed(text: "line\(line)\r\n")
        }
        terminal.layoutIfNeeded()

        // Simulate the state SwiftTerm 1.15 leaves behind when a fling coasts
        // past a screen and stops short of the bottom: yDisp frozen at the
        // finger-lift row while the actual content — and the row a touch
        // names — has moved on.
        terminal.getTerminal().buffer.yDisp = 0
        let touched = rows + extra - 1

        let began = terminal.beginSelectionForTest(at: point(row: touched, col: 1, on: terminal))

        XCTAssertFalse(
            began,
            "the touched row sits outside bufferLine's yDisp-bounded window, so this must fail closed rather than silently landing on line\(rows - 1) — the pre-fix `yDisp + rows - 1` clamp target"
        )
        XCTAssertNil(terminal.selectionController.selectedText())
    }

    // MARK: - Keyboard regression

    func testSelectingNeverRaisesTheKeyboard() {
        let terminal = makeTerminal()
        terminal.feed(text: "hello world\r\n")

        terminal.beginSelectionForTest(at: point(row: 0, col: 1, on: terminal))
        _ = terminal.selectionController.selectedText()

        XCTAssertFalse(terminal.keyboardGate, "selection must not open the explicit-keyboard gate")
        XCTAssertFalse(terminal.isFirstResponder, "no part of the selection path may become first responder")
    }
}

private extension MobaideTerminalView {
    /// Thin alias so the tests read as behavior, not plumbing.
    @discardableResult
    func beginSelectionForTest(at point: CGPoint) -> Bool {
        selectionController.beginSelection(at: point)
    }
}
