import UIKit
import XCTest
@testable import Mobaide

@MainActor
final class TerminalHostGestureTests: XCTestCase {
    private func makeModel() -> TerminalSessionModel {
        TerminalTestSupport.model()
    }

    private func makeTerminal() -> MobaideTerminalView {
        MobaideTerminalView(frame: .zero)
    }

    private func recognizers(named name: String, on view: UIView) -> [UIGestureRecognizer] {
        (view.gestureRecognizers ?? []).filter { $0.name == name }
    }

    // MARK: - neutralizeSwiftTermGestures

    func testDisablesPlainPanButKeepsScrollViewPan() {
        let terminal = makeTerminal()
        // Simulates the recognizer SwiftTerm adds lazily while a TUI enables
        // mouse reporting; it swallowed every drag in Claude sessions.
        let mouseReportPan = UIPanGestureRecognizer()
        terminal.addGestureRecognizer(mouseReportPan)
        let scrollbackDriver = terminal.panGestureRecognizer

        terminal.neutralizeSwiftTermGestures()

        XCTAssertFalse(mouseReportPan.isEnabled, "the mouse-reporting pan must be disabled")
        XCTAssertTrue(scrollbackDriver.isEnabled, "the scrollback pan must stay alive")
    }

    func testDisablesAllSwiftTermTapsAndSelectionGesturesButKeepsMobaideRecognizers() {
        let terminal = makeTerminal()
        let longPress = UILongPressGestureRecognizer()
        let tripleTap = UITapGestureRecognizer()
        tripleTap.numberOfTapsRequired = 3
        let singleTap = UITapGestureRecognizer()
        singleTap.numberOfTapsRequired = 1
        let mobaideDoubleTap = UITapGestureRecognizer()
        mobaideDoubleTap.numberOfTapsRequired = 2
        mobaideDoubleTap.name = "mobaide.rowDoubleTap"
        for recognizer in [longPress, tripleTap, singleTap, mobaideDoubleTap] {
            terminal.addGestureRecognizer(recognizer)
        }

        terminal.neutralizeSwiftTermGestures()

        XCTAssertFalse(longPress.isEnabled)
        XCTAssertFalse(tripleTap.isEnabled)
        XCTAssertFalse(
            singleTap.isEnabled,
            "SwiftTerm's single-tap calls becomeFirstResponder — every SwiftTerm tap must be disabled"
        )
        XCTAssertTrue(mobaideDoubleTap.isEnabled, "mobaide.* recognizers are exempt")
    }

    func testMouseModeChangedRerunsNeutralization() {
        let terminal = makeTerminal()
        terminal.neutralizeSwiftTermGestures()
        // Added after the sweep, as SwiftTerm does on a mouse-mode transition.
        let latePan = UIPanGestureRecognizer()
        terminal.addGestureRecognizer(latePan)
        XCTAssertTrue(latePan.isEnabled)

        terminal.mouseModeChanged(source: terminal.getTerminal())

        XCTAssertFalse(latePan.isEnabled, "lazily added pans must be swept on mouse-mode changes")
    }

    // MARK: - Vertical-axis arbitration

    /// Drives the emulator the way a real TUI does, so the arbitration is read
    /// from live state instead of a flag someone has to remember to re-assert.
    private func enterAlternateScreen(_ terminal: MobaideTerminalView) {
        terminal.feed(text: "\u{1B}[?1049h")
    }

    private func enableMouseReporting(_ terminal: MobaideTerminalView) {
        terminal.feed(text: "\u{1B}[?1000h\u{1B}[?1006h")
    }

    func testAlternateScreenWithMouseReportingTakesTheAxisFromScrollback() {
        let terminal = makeTerminal()
        terminal.configureAppearance()
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)

        XCTAssertEqual(terminal.wheelScrollMode, .wheel)
        XCTAssertFalse(
            terminal.gestureRecognizerShouldBegin(terminal.panGestureRecognizer),
            "the scrollback pan must not also run — the alternate buffer has no scrollback to move"
        )
    }

    func testPlainShellKeepsTheScrollbackPan() {
        let terminal = makeTerminal()
        terminal.configureAppearance()

        XCTAssertEqual(terminal.wheelScrollMode, .localScrollback)
        XCTAssertTrue(
            terminal.gestureRecognizerShouldBegin(terminal.panGestureRecognizer),
            "the untouched normal-buffer path: SwiftTerm's own scroll view still owns the drag"
        )
    }

    // MARK: - Horizontal-axis handoff

    /// A near-diagonal drag with horizontal drift: the shape the removed
    /// directional lock used to latch onto an axis the inner view cannot
    /// scroll, killing the scrollback for the rest of the touch.
    private static let horizontallyDominantDrag = CGPoint(x: 90, y: 12)

    /// Its mirror — a vertical drag carrying sideways drift.
    private static let verticallyDominantDrag = CGPoint(x: 12, y: 90)

    /// Mounts the terminal in a real wrapper, so the child half resolves its
    /// host through the live view hierarchy the way a touch does.
    private func hostedTerminal(mode: TerminalDisplayMode) -> (TerminalPanScrollView, MobaideTerminalView) {
        let terminal = makeTerminal()
        terminal.configureAppearance()
        let scroll = TerminalPanScrollView(
            terminalView: terminal,
            displayConfig: TerminalDisplayConfig(mode: mode, staticColumns: 120)
        )
        scroll.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        scroll.layoutIfNeeded()
        // Second pass with a settled cellSize, which the Static width derives from.
        scroll.setNeedsLayout()
        scroll.layoutIfNeeded()
        feedTranscript(terminal)
        XCTAssertGreaterThan(terminal.maxScrollOffsetY, 0, "the harness must have real scrollback for the pan to be worth beginning")
        return (scroll, terminal)
    }

    /// The dead-lock fix and the polarity guard in one: the identical sample
    /// has to come out the other way once the host stops arbitrating. Inverting
    /// the `outerClaimsHorizontalPan:` argument at the call site flips both
    /// answers, so this pair and the Static refusal above fail together.
    func testFullModeHostKeepsTheHorizontalDragTheStaticHostRefuses() {
        let (fullScroll, fullTerminal) = hostedTerminal(mode: .full)
        let (staticScroll, staticTerminal) = hostedTerminal(mode: .static)
        XCTAssertTrue(fullTerminal.superview === fullScroll, "the host is resolved per touch from the live hierarchy")
        XCTAssertTrue(staticTerminal.superview === staticScroll)

        XCTAssertTrue(
            fullTerminal.allowsScrollbackPan(velocity: Self.horizontallyDominantDrag, translation: .zero),
            "Full mode has no horizontal overflow and never arbitrates, so a drag starting with horizontal drift must still reach the scrollback"
        )
        XCTAssertFalse(
            staticTerminal.allowsScrollbackPan(velocity: Self.horizontallyDominantDrag, translation: .zero),
            "and the mode gate is what makes the difference — one sample, two hosts, opposite answers"
        )
    }

    func testHostedAlternateBufferRefusesEvenTheDragsTheOuterLeavesAlone() {
        let (scroll, terminal) = hostedTerminal(mode: .static)
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)

        XCTAssertTrue(terminal.superview === scroll, "the host is resolved per touch from the live hierarchy")
        XCTAssertEqual(terminal.wheelScrollMode, .wheel)
        XCTAssertFalse(
            terminal.allowsScrollbackPan(velocity: Self.verticallyDominantDrag, translation: .zero),
            "the wheel ladder outranks the horizontal split: a drag the outer would leave alone is still not the scrollback's here"
        )
    }

    // MARK: - Vertical-scroll recognizer lifecycle

    func testVerticalScrollGestureSurvivesAScreenRevisit() {
        let model = makeModel()
        let terminal = model.terminalView

        TerminalHostView.Coordinator(model: model).attachGestures(to: terminal)
        let first = recognizers(named: MobaideTerminalView.verticalScrollGestureName, on: terminal)
        TerminalHostView.Coordinator(model: model).attachGestures(to: terminal)

        let second = recognizers(named: MobaideTerminalView.verticalScrollGestureName, on: terminal)
        XCTAssertEqual(first.count, 1)
        XCTAssertEqual(second.count, 1)
        XCTAssertTrue(
            first[0] === second[0],
            "the recognizer targets the view, not the per-visit Coordinator — sweeping it would kill scrolling on revisit"
        )
    }

    func testNeutralizationSparesTheVerticalScrollGesture() {
        let terminal = makeTerminal()
        terminal.configureAppearance()

        terminal.neutralizeSwiftTermGestures()

        let pan = recognizers(named: MobaideTerminalView.verticalScrollGestureName, on: terminal).first
        XCTAssertEqual(
            pan?.isEnabled, true,
            "it is exactly a UIPanGestureRecognizer, so only the mobaide.* exemption keeps it alive"
        )
    }

    // MARK: - emitVerticalScroll

    /// SwiftTerm holds `terminalDelegate` weakly, so the Coordinator that
    /// carries emitted bytes into the model has to outlive the helper.
    private var retainedCoordinator: TerminalHostView.Coordinator?

    private func poll(_ predicate: () -> Bool) async -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if predicate() { return true }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return predicate()
    }

    /// A model wired to its Coordinator exactly as `makeUIView` does, so
    /// emitted bytes travel the real chain: terminal → delegate → PTY frame.
    private func wiredModel(running: Bool = true) async throws -> (TerminalSessionModel, FakeWebSocketConnection) {
        let factory = FakeWebSocketTransportFactory()
        let model = TerminalTestSupport.model(transportFactory: factory)
        let coordinator = TerminalHostView.Coordinator(model: model)
        retainedCoordinator = coordinator
        model.terminalView.terminalDelegate = coordinator
        // An unlaid-out view leaves SwiftTerm on a one-row grid, and the
        // emitter caps a callback at one page — so give it a real phone grid.
        model.terminalView.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        model.terminalView.layoutIfNeeded()
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        guard running else { return (model, connection) }
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        let ready = await poll { model.connectionState == .live && model.isRunning }
        XCTAssertTrue(ready, "the harness must reach live+running before bytes can flow")
        return (model, connection)
    }

    private func inputFrames(_ connection: FakeWebSocketConnection) -> [Data] {
        connection.sentBinaries.filter { $0.first == 0x69 }
    }

    private func waitForInputFrames(_ connection: FakeWebSocketConnection, atLeast count: Int) async -> [Data] {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if inputFrames(connection).count >= count { return inputFrames(connection) }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return inputFrames(connection)
    }

    private func inputFrame(_ text: String) -> Data {
        Data([0x69]) + Data(text.utf8)
    }

    /// Wheel reports carried by one input frame, which batching now packs many
    /// to a frame.
    private func wheelReportCount(in frame: Data) -> Int {
        let payload = String(decoding: frame.dropFirst(), as: UTF8.self)
        return payload.components(separatedBy: "\u{1B}[<").count - 1
    }

    private func wheelReportCount(in frames: [Data]) -> Int {
        frames.reduce(0) { $0 + wheelReportCount(in: $1) }
    }

    func testWheelModeBatchesOneReportPerRowIntoOneWrite() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)
        let anchor = CGPoint(x: terminal.cellSize.width * 5.5, y: terminal.cellSize.height * 3.5)

        terminal.emitVerticalScroll(rowsDown: 3, at: anchor)

        let frames = await waitForInputFrames(connection, atLeast: 1)
        let report = "\u{1B}[<65;6;4M"
        XCTAssertEqual(
            frames, [inputFrame(String(repeating: report, count: 3))],
            "three rows are three wheel-down reports at the cell the drag began on, but one frame on the wire"
        )
    }

    func testCursorKeyModeBatchesOneArrowPerRowIntoOneWrite() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)

        terminal.emitVerticalScroll(rowsDown: 2, at: .zero)

        // One arrow per scrolled row, but a single write: the PTY is a byte
        // stream, so the program cannot tell the difference, while the wire
        // carries one frame instead of one per row.
        let frames = await waitForInputFrames(connection, atLeast: 1)
        XCTAssertEqual(frames, [inputFrame("\u{1B}[B\u{1B}[B")])
    }

    func testApplicationCursorModeSwapsCsiForSs3() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        terminal.feed(text: "\u{1B}[?1h") // DECCKM, as pagers set on entry

        terminal.emitVerticalScroll(rowsDown: -1, at: .zero)

        let frames = await waitForInputFrames(connection, atLeast: 1)
        XCTAssertEqual(frames, [inputFrame("\u{1B}OA")])
    }

    func testOneCallbackNeverEmitsMoreThanOnePage() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)
        let rows = terminal.getTerminal().getDims().rows
        XCTAssertGreaterThan(rows, 3, "the cap is only meaningful against a real page of rows")

        terminal.emitVerticalScroll(rowsDown: 1000, at: .zero)

        let frames = await waitForInputFrames(connection, atLeast: 1)
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(inputFrames(connection).count, 1, "one callback is one batched write")
        XCTAssertEqual(
            frames.first.map { wheelReportCount(in: $0) }, rows,
            "a stalled main thread delivers one huge delta; each report costs the program a full repaint"
        )
    }

    func testEmissionSwitchesLadderArmWhenTheProgramTurnsMouseReportingOn() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)

        terminal.emitVerticalScroll(rowsDown: 1, at: .zero)
        _ = await waitForInputFrames(connection, atLeast: 1)
        enableMouseReporting(terminal)
        terminal.emitVerticalScroll(rowsDown: 1, at: .zero)

        let frames = await waitForInputFrames(connection, atLeast: 2)
        XCTAssertEqual(
            frames, [inputFrame("\u{1B}[B"), inputFrame("\u{1B}[<65;1;1M")],
            "the arm is re-derived per emit, so output arriving mid-drag changes what the next row sends"
        )
    }

    func testWheelReportsAreDroppedWhileTheSessionIsNotLive() async throws {
        let (model, connection) = try await wiredModel(running: false)
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)

        terminal.emitVerticalScroll(rowsDown: 2, at: .zero)

        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            inputFrames(connection).isEmpty,
            "wheel reports travel the gated terminal-byte path, like every other terminal-originated byte"
        )
    }

    // MARK: - Fling

    /// A flick strong enough to saturate the `±rows/2` clamp, so the expected
    /// travel does not depend on the harness's measured cell height.
    private let hardFlickUp = CGPoint(x: 0, y: -20_000)

    /// Emitted rows reach the fake socket through the model's outbound stream,
    /// so a frame count read the instant the fling ends is still short.
    private func drain() async {
        try? await Task.sleep(for: .milliseconds(50))
    }

    private func flungTerminal(_ terminal: MobaideTerminalView) async {
        terminal.startFling(velocity: hardFlickUp)
        await terminal.flingTask?.value
        await drain()
    }

    /// Runs a fling into its third frame, stops it the given way, and returns
    /// the rows that had already been emitted once everything has settled.
    private func interruptedFling(
        _ terminal: MobaideTerminalView,
        _ connection: FakeWebSocketConnection,
        by stop: (MobaideTerminalView) -> Void
    ) async -> Int {
        terminal.startFling(velocity: hardFlickUp)
        try? await Task.sleep(for: TerminalWheelScroll.flingFrameInterval * 2)
        stop(terminal)
        await terminal.flingTask?.value
        await drain()
        return wheelReportCount(in: inputFrames(connection))
    }

    func testFlingKeepsScrollingAfterTheFingerLifts() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)

        await flungTerminal(terminal)

        let frames = inputFrames(connection)
        XCTAssertGreaterThan(
            wheelReportCount(in: frames), 1,
            "momentum is the whole point: a flick has to outrun the finger that started it"
        )
        XCTAssertGreaterThan(frames.count, 1, "and it has to arrive spread over time, not as one jump")
    }

    func testFlingStopsWhenTheProgramLeavesTheAlternateScreen() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)

        let emitted = await interruptedFling(terminal, connection) {
            $0.feed(text: "\u{1B}[?1000l\u{1B}[?1049l")
        }

        XCTAssertGreaterThan(emitted, 0, "the abort must cut a fling short, not stop it from ever starting")
        XCTAssertLessThan(
            wheelReportCount(in: inputFrames(connection)), terminal.getTerminal().getDims().rows / 2,
            "Android aborts on a mouse-tracking flip; the rows left over must not land on whatever replaced the TUI"
        )
    }

    func testNewTouchCancelsAnInFlightFling() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)

        let caught = await interruptedFling(terminal, connection) {
            $0.touchesBegan(Set<UITouch>(), with: nil)
        }

        try await Task.sleep(for: TerminalWheelScroll.flingFrameInterval * 6)
        XCTAssertEqual(
            wheelReportCount(in: inputFrames(connection)), caught,
            "tap-to-stop, the UIScrollView convention — Android has none and always runs the fling out"
        )
        XCTAssertLessThan(caught, terminal.getTerminal().getDims().rows / 2, "the fling must have been cut short")
    }

    // MARK: - dismantleUIView

    func testDismantleDetachesTerminalWhenWrapperStillOwnsIt() {
        let model = makeModel()
        let coordinator = TerminalHostView.Coordinator(model: model)
        let config = TerminalDisplayConfig(mode: .static, staticColumns: 120)
        let wrapper = TerminalPanScrollView(terminalView: model.terminalView, displayConfig: config)

        TerminalHostView.dismantleUIView(wrapper, coordinator: coordinator)

        XCTAssertNil(model.terminalView.superview)
    }

    func testDismantleLeavesTerminalWithTheWrapperThatStoleIt() {
        let model = makeModel()
        let coordinator = TerminalHostView.Coordinator(model: model)
        let config = TerminalDisplayConfig(mode: .static, staticColumns: 120)
        let oldWrapper = TerminalPanScrollView(terminalView: model.terminalView, displayConfig: config)
        let newWrapper = TerminalPanScrollView(terminalView: model.terminalView, displayConfig: config)

        TerminalHostView.dismantleUIView(oldWrapper, coordinator: coordinator)

        XCTAssertTrue(
            model.terminalView.superview === newWrapper,
            "a dying wrapper must not detach a terminal a newer wrapper already owns"
        )
    }

    // MARK: - Follow mode

    /// Buffer index of the prompt line `feedTranscript` puts into history.
    private static let historyPromptRow = 6

    /// More output than one screen holds, so `contentSize` really exceeds the
    /// viewport and there is scrollback to leave — plus a prompt row parked in
    /// history and another one left under the cursor for row hit-testing.
    private func feedTranscript(_ terminal: MobaideTerminalView) {
        var text = ""
        for index in 0..<200 {
            text += index == Self.historyPromptRow ? "> echo history\r\n" : "line \(index)\r\n"
        }
        terminal.feed(text: text + "> echo tail")
    }

    private func layOutWithScrollback(_ terminal: MobaideTerminalView) {
        // An unlaid-out view leaves SwiftTerm on a one-row grid whose whole
        // buffer fits the viewport — there would be no scroll range at all, and
        // every position would read as the tail.
        terminal.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        terminal.layoutIfNeeded()
        feedTranscript(terminal)
        XCTAssertGreaterThan(terminal.maxScrollOffsetY, 0, "the harness must have real scrollback to leave")
    }

    private func scrolledBackTerminal() -> MobaideTerminalView {
        let terminal = makeTerminal()
        terminal.configureAppearance()
        layOutWithScrollback(terminal)
        return terminal
    }

    /// A model wired to its Coordinator exactly as `makeUIView` does, so both
    /// scroll chains are real: a drag travels scroll view → view → model, and an
    /// emulator scroll travels `Terminal.scroll` → `TerminalView.scrolled` →
    /// Coordinator → model. SwiftTerm holds `terminalDelegate` weakly, hence the
    /// retained Coordinator.
    private func scrollableModel() -> TerminalSessionModel {
        let model = makeModel()
        let coordinator = TerminalHostView.Coordinator(model: model)
        retainedCoordinator = coordinator
        model.terminalView.terminalDelegate = coordinator
        coordinator.attachGestures(to: model.terminalView)
        layOutWithScrollback(model.terminalView)
        return model
    }

    private func scrollUp(_ terminal: MobaideTerminalView) {
        terminal.contentOffset = CGPoint(x: 0, y: terminal.maxScrollOffsetY / 2)
        terminal.handleScrollViewDidScroll(isUserDriven: true)
    }

    /// `getLine(row:)` counts from `yDisp`, so `row` is a screen row rather than
    /// a buffer index — which is what an in-place screen rotation like RI has to
    /// be read through.
    private func visibleRow(_ terminal: MobaideTerminalView, _ row: Int) -> String {
        (terminal.getTerminal().getLine(row: row)?.translateToString(trimRight: true) ?? "")
            .trimmingCharacters(in: .whitespaces)
    }

    func testUserDrivenScrollIntoHistoryPausesFollowMode() {
        let model = scrollableModel()
        let terminal = model.terminalView

        scrollUp(terminal)

        XCTAssertFalse(model.followMode, "the whole defect: a drag never reached the model")
        XCTAssertTrue(
            terminal.isAutoScrollSuppressed,
            "a paused viewport must also stop being yanked back to the tail"
        )
    }

    func testProgrammaticOffsetWriteNeverMovesFollowMode() {
        let model = scrollableModel()
        let terminal = model.terminalView

        terminal.contentOffset = .zero

        XCTAssertEqual(terminal.contentOffset.y, 0, accuracy: 0.5, "the write must actually land")
        XCTAssertTrue(
            model.followMode,
            "UIKit delivers scrollViewDidScroll for plain assignments too, so the guard has to live in the observer"
        )
    }

    func testSuppressedViewportHoldsItsOffsetWhileOutputArrives() {
        let model = scrollableModel()
        let terminal = model.terminalView
        scrollUp(terminal)
        let held = terminal.contentOffset.y
        XCTAssertLessThan(held, terminal.maxScrollOffsetY, "the viewport has to be away from the tail to be held there")

        terminal.feed(text: "fresh output\r\n")

        XCTAssertEqual(
            terminal.contentOffset.y, held, accuracy: 0.5,
            "SwiftTerm's updateScroller snaps to the tail on every scrolled line; a paused reader must not be yanked along"
        )
    }

    func testJumpToBottomLandsAtTheTailAndClearsTheUnreadTally() {
        let model = scrollableModel()
        let terminal = model.terminalView
        scrollUp(terminal)
        model.onEmulatorScrolled()
        model.onEmulatorScrolled()
        XCTAssertEqual(model.unreadRows, 2)

        model.jumpToBottom()

        XCTAssertEqual(
            terminal.contentOffset.y, terminal.maxScrollOffsetY, accuracy: 0.5,
            "scroll(toPosition: 1.0) is guarded by `row != yDisp`, which is already the target row here, so the button did nothing"
        )
        XCTAssertTrue(model.followMode)
        XCTAssertEqual(model.unreadRows, 0)
        XCTAssertFalse(terminal.isAutoScrollSuppressed)
    }

    /// The tally has one and only one producer — the SwiftTerm delegate chain —
    /// so calling `onEmulatorScrolled()` by hand proves nothing about whether
    /// real output ever reaches it. Feeding actual bytes is what closes the same
    /// blind spot that let the follow-mode defect ship green.
    func testEmulatorOutputWhilePausedAccruesOneUnreadRowPerScrolledLine() {
        let model = scrollableModel()
        let terminal = model.terminalView
        scrollUp(terminal)
        XCTAssertEqual(model.unreadRows, 0, "output before this point arrived while following")
        // `scrollUp` writes `contentOffset` with no live touch, so `isTracking`
        // is false, SwiftTerm never sets `Terminal.userScrolling`, and `yDisp`
        // stays pinned to `yBase` — which is what lets its delta count the lines
        // the emulator pushed into the scrollback. A real finger would freeze it.
        let originBefore = terminal.getTerminal().buffer.yDisp

        terminal.feed(text: "a\r\nb\r\n")

        XCTAssertEqual(
            terminal.getTerminal().buffer.yDisp - originBefore, 2,
            "two line feeds on the bottom row push exactly two lines past the bottom"
        )
        XCTAssertEqual(
            model.unreadRows, 2,
            "Terminal.scroll → TerminalView.scrolled → Coordinator.scrolled → onEmulatorScrolled, once per pushed line"
        )
    }

    /// A DECSTBM region scroll reports through the very same `scrolled` callback
    /// as an ordinary one, and used to be counted: three line feeds inside
    /// `ESC[2;5r` reached a tally of 3 while the tail had not moved at all. The
    /// second half is what keeps the new gate honest — a predicate that answered
    /// `false` to everything would pass the first three assertions alone.
    func testRegionScrollWhilePausedAccruesNothingButFullWidthOutputStillCounts() {
        let model = scrollableModel()
        let terminal = model.terminalView
        scrollUp(terminal)
        XCTAssertEqual(model.unreadRows, 0, "output before this point arrived while following")
        let originBefore = terminal.getTerminal().buffer.yDisp

        terminal.feed(text: "\u{1B}[2;5r\u{1B}[5;1H")
        terminal.feed(text: "x\r\ny\r\nz\r\n")

        XCTAssertEqual(
            terminal.getTerminal().buffer.yDisp, originBefore,
            "three line feeds on the bottom row of a rows 2-5 region push nothing past the bottom"
        )
        XCTAssertEqual(
            model.unreadRows, 0,
            "the badge promised three rows the reader could never scroll back to"
        )

        // DECSTBM homes the cursor, so it has to be walked back down to the last
        // row before a line feed can scroll anything again.
        let rows = terminal.getTerminal().getDims().rows
        terminal.feed(text: "\u{1B}[r\u{1B}[\(rows);1H")
        terminal.feed(text: "d\r\ne\r\n")

        XCTAssertEqual(
            terminal.getTerminal().buffer.yDisp - originBefore, 2,
            "back on the full screen the tail moves again, two lines' worth"
        )
        XCTAssertEqual(
            model.unreadRows, 2,
            "and the tally has to move with it — a gate that counts nothing is not a fix"
        )
    }

    /// The scrollback gate reads no margin value at all — see
    /// `TerminalFollowScroll.pushesRowIntoScrollback` for why neither one stands
    /// in for the `marginMode` SwiftTerm actually branches on. This reproduces
    /// the strand that makes them wrong, so a reintroduced margin term fails
    /// here with the reason attached rather than as a bare count downstream.
    ///
    /// Both margins strand from the same `ESC[?69h...s...ESC[?69l` sequence
    /// (`cmdSetMargins` sets `marginLeft`/`marginRight` together): after
    /// `ESC[?69l`, SwiftTerm is back on the arm that really does fill the
    /// scrollback, so a `marginLeft == 0` term would pin the badge at zero for
    /// good.
    func testStrandedMarginsDoNotSuppressTheUnreadTally() {
        let model = scrollableModel()
        let terminal = model.terminalView
        let buffer = terminal.getTerminal().buffer
        // DECLRMM on, both margins set narrower than the grid, DECLRMM back
        // off: margin mode is false again but the margins keep their values,
        // and nothing ever restores them to full width.
        terminal.feed(text: "\u{1B}[?69h\u{1B}[3;40s\u{1B}[?69l")
        XCTAssertGreaterThan(
            buffer.marginLeft, 0,
            "ESC[?69l drops margin mode without restoring the margins it was gating"
        )
        XCTAssertLessThan(
            buffer.marginRight, terminal.getTerminal().getDims().cols - 1,
            "the harness has to reproduce the strand, or there is no hazard here to guard"
        )
        scrollUp(terminal)
        let originBefore = buffer.yDisp

        // Plain CR+LF from the bottom row, which is what every program emits.
        // The carriage return parks the cursor at column 0 — left of the
        // stranded `marginLeft`, the state that used to make SwiftTerm drop the
        // line feed on the floor.
        terminal.feed(text: "a\r\n")

        XCTAssertEqual(
            buffer.yDisp - originBefore, 1,
            "a line feed from column 0 must scroll: stranded margins gate nothing once margin mode is off"
        )
        XCTAssertEqual(
            model.unreadRows, 1,
            "margins stranded by ESC[?69l must not decide what a full-size session counts"
        )
    }

    // MARK: - Emulator auto-replies

    private func outputFrame(_ text: String) -> Data {
        Data([0x6F]) + Data(text.utf8)
    }

    /// `wiredModel` wires SwiftTerm's own delegate but not the scroll-view
    /// callback `makeUIView` installs alongside it, and follow mode moves
    /// through that callback alone — so the pause has to travel the real drag
    /// chain rather than being poked into the model.
    private func pausedWiredModel() async throws
        -> (TerminalSessionModel, FakeWebSocketConnection, TerminalHostView.Coordinator) {
        let (model, connection) = try await wiredModel()
        let coordinator = try XCTUnwrap(retainedCoordinator)
        coordinator.attachGestures(to: model.terminalView)
        layOutWithScrollback(model.terminalView)
        scrollUp(model.terminalView)
        XCTAssertFalse(model.followMode, "the harness must be paused before anything is delivered to it")
        return (model, connection, coordinator)
    }

    /// SwiftTerm answers `ESC[6n` (DSR — cursor position) synchronously while
    /// it parses inbound output, and the reply travels the same
    /// `TerminalViewDelegate.send` chain a typed key does. Prompt frameworks
    /// emit that query routinely, so a reply that resumed follow mode would let
    /// a program yank a paused reader back to the tail on its own. The bytes go
    /// in through the `o` frame rather than `feed` because `handleBinary` is
    /// where the emulator-output attribution is raised.
    func testEmulatorAutoReplyReachesThePtyWithoutResumingFollowMode() async throws {
        let (model, connection, _) = try await pausedWiredModel()
        let terminal = model.terminalView
        connection.emit(.binary(outputFrame("a\r\nb\r\n")))
        let accrued = await poll { model.unreadRows == 2 }
        XCTAssertTrue(accrued, "the paused viewport needs a real tally before the query, or clearing it would not show")

        connection.emit(.binary(outputFrame("\u{1B}[5;3H\u{1B}[6n")))

        let frames = await waitForInputFrames(connection, atLeast: 1)
        XCTAssertEqual(
            frames, [inputFrame("\u{1B}[5;3R")],
            "only the follow-mode resume is dropped — the cursor-position report still has to reach the PTY"
        )
        XCTAssertFalse(model.followMode, "a program answering its own query is not the user returning to the tail")
        XCTAssertEqual(model.unreadRows, 2, "resuming would have wiped the tally the reader has not caught up on")
        XCTAssertTrue(
            terminal.isAutoScrollSuppressed,
            "and lifted the hold that keeps the viewport where the finger left it"
        )
    }

    /// The gate is scoped to bytes the emulator generated while parsing; the
    /// identical delegate call outside an output frame is a real keystroke and
    /// must still resume.
    func testTypedKeyOutsideAnOutputFrameStillResumesFollowMode() async throws {
        let (model, connection, coordinator) = try await pausedWiredModel()
        let terminal = model.terminalView
        connection.emit(.binary(outputFrame("a\r\n")))
        let accrued = await poll { model.unreadRows == 1 }
        XCTAssertTrue(accrued, "the tally has to be non-zero for the clear to be observable")

        coordinator.send(source: terminal, data: Array("ls".utf8)[...])

        let frames = await waitForInputFrames(connection, atLeast: 1)
        XCTAssertEqual(frames, [inputFrame("ls")])
        XCTAssertTrue(model.followMode, "typing is a resume trigger — the answer has to be visible")
        XCTAssertEqual(model.unreadRows, 0)
        XCTAssertFalse(terminal.isAutoScrollSuppressed)
    }

    /// OSC 52 has a query direction (`ESC]52;c;?ST`) that answers with the
    /// user's clipboard, base64'd, straight back down the same reply chain as
    /// the cursor-position report below it. Anything running on the agent host
    /// can send it, so the delegate has to refuse — the pasteboard is not the
    /// session's to read. The DSR query rides along so the absence of a
    /// clipboard reply is pinned by a frame that did arrive rather than by a
    /// timeout.
    func testOscClipboardQueryIsRefusedWhileOtherRepliesStillReachThePty() async throws {
        let (model, connection, _) = try await pausedWiredModel()
        Clipboard.copy("secret", isSensitive: true)

        connection.emit(.binary(outputFrame("\u{1B}[5;3H\u{1B}]52;c;?\u{1B}\\\u{1B}[6n")))

        let frames = await waitForInputFrames(connection, atLeast: 1)
        XCTAssertEqual(
            frames, [inputFrame("\u{1B}[5;3R")],
            "the cursor report is the only thing that may come back — a clipboard read would be exfiltration"
        )
        XCTAssertFalse(
            frames.contains { String(decoding: $0, as: UTF8.self).contains("52;") },
            "no OSC 52 response in any shape, including an empty one"
        )
        XCTAssertFalse(model.followMode, "a program querying the clipboard is not the user returning to the tail")
    }

    /// The refusal above only means something if SwiftTerm asks in the first
    /// place: a silent parse change would leave that test green while nothing
    /// ever reached the delegate. Both directions land in the same
    /// `oscClipboard` handler, so a write that arrives is the oracle that the
    /// OSC 52 route into this app is live — and it pins the direction the
    /// refusal deliberately left open, which 1.15.0 widened to selection
    /// characters 1.13.0 ignored.
    ///
    /// Scope: this feeds `terminalView.feed` directly, bypassing `handleBinary` and
    /// `applyingEmulatorOutput(replayed:)`, so it says nothing about replay attribution —
    /// that is pinned by the two wire-path tests below. Do not delete it as a duplicate of
    /// them: it is the only oracle that SwiftTerm still routes OSC 52 into the delegate.
    func testOscClipboardWriteStillReachesTheCopyPath() {
        let model = scrollableModel()
        UIPasteboard.general.items = []

        model.terminalView.feed(text: "\u{1B}]52;c;aGVsbG8=\u{1B}\\")

        XCTAssertEqual(
            UIPasteboard.general.string, "hello",
            "OSC 52 writes still travel emulator → Coordinator → pasteboard; only the read direction is refused"
        )
    }

    /// The bytes go in through the wire rather than `feed` because the replay attribution
    /// is raised in `handleBinary`, and the visible tail is what pins the negative: the
    /// frame really was parsed, only its clipboard write was refused.
    func testOscClipboardWriteInsideAReplaySpanNeverReachesThePasteboard() async throws {
        let (model, connection) = try await wiredModel()
        UIPasteboard.general.items = []

        connection.emit(.binary(Data([TerminalFrameType.replayStart])))
        connection.emit(.binary(outputFrame("\u{1B}]52;c;cmVwbGF5ZWQ=\u{1B}\\HISTORY")))
        connection.emit(.binary(Data([TerminalFrameType.replayEnd])))

        let painted = await poll { visibleRow(model.terminalView, 0) == "HISTORY" }
        XCTAssertTrue(painted, "the replayed screen content still has to land — only the clipboard write is dropped")
        XCTAssertNil(
            UIPasteboard.general.string,
            "a yank the user performed some other day is not a yank the user just performed"
        )
    }

    /// The other half: attribution, not a blanket refusal of the OSC 52 route.
    /// A write outside a replay span is the user's own yank and still lands.
    func testOscClipboardWriteFromLiveOutputStillReachesThePasteboard() async throws {
        let (_, connection) = try await wiredModel()
        UIPasteboard.general.items = []

        connection.emit(.binary(outputFrame("\u{1B}]52;c;eWFua2Vk\u{1B}\\")))

        let copied = await poll { UIPasteboard.general.string == "yanked" }
        XCTAssertTrue(copied, "live OSC 52 writes are the half of the feature this app keeps")
    }

    // MARK: - resolveRow

    func testResolveRowReadsTheRenderedLineWhileScrolledIntoHistory() {
        let terminal = scrolledBackTerminal()
        let cellHeight = terminal.cellSize.height
        let yDispBefore = terminal.getTerminal().buffer.yDisp
        terminal.contentOffset = .zero

        let resolved = terminal.resolveRow(
            at: CGPoint(x: 20, y: CGFloat(Self.historyPromptRow) * cellHeight + cellHeight / 2)
        )

        XCTAssertEqual(
            resolved?.text, "echo history",
            "getLine counts from the tail-pinned yDisp while the renderer counts from contentOffset"
        )
        XCTAssertEqual(resolved?.isCurrentInput, false, "scrolled into history, every prompt is historical")
        XCTAssertEqual(
            terminal.getTerminal().buffer.yDisp, yDispBefore,
            "the re-pointed read window must be restored before any SwiftTerm code observes it"
        )
    }

    func testOverscrolledTapBelowTheContentResolvesNothing() {
        let terminal = scrolledBackTerminal()
        let cellHeight = terminal.cellSize.height
        let yDispBefore = terminal.getTerminal().buffer.yDisp
        let lastRow = terminal.getTerminal().getDims().rows - 1
        // A rubber-band bounce at the tail drives the offset past the content.
        // SwiftTerm's own renderer draws nothing past the last real line
        // (`AppleTerminalView.drawTerminalContents` skips rows past the
        // buffer), so this tap — ten rows below the last line — lands on
        // blank background, not on "echo tail" folded up to meet it.
        terminal.contentOffset = CGPoint(x: 0, y: terminal.maxScrollOffsetY + 10 * cellHeight)

        let resolved = terminal.resolveRow(
            at: CGPoint(x: 20, y: terminal.contentOffset.y + CGFloat(lastRow) * cellHeight + cellHeight / 2)
        )

        XCTAssertNil(resolved, "a rubber-band bounce draws blank below the last line; a tap there must not resolve a command")
        XCTAssertEqual(
            terminal.getTerminal().buffer.yDisp, yDispBefore,
            "the read window must be restored even when nothing resolves"
        )
    }

    /// A drag routinely leaves `contentOffset` mid-cell rather than on a cell
    /// boundary. The pre-fix math floored the viewport row and the window
    /// origin separately — `floor(0.6) + floor(0.5)` here — landing one row
    /// short of the tap; a single floor of the absolute position —
    /// `floor(0.6 + 0.5)` — lands on the row actually under the finger.
    func testNonCellAlignedOffsetResolvesTheRowUnderTheFingerNotOneRowShort() {
        let terminal = scrolledBackTerminal()
        let cellHeight = terminal.cellSize.height
        let topRow = Self.historyPromptRow - 1
        terminal.contentOffset = CGPoint(x: 0, y: (CGFloat(topRow) + 0.6) * cellHeight)

        let resolved = terminal.resolveRow(
            at: CGPoint(x: 20, y: terminal.contentOffset.y + 0.5 * cellHeight)
        )

        XCTAssertEqual(
            resolved?.text, "echo history",
            "the tap lands 1.1 cells below row \(topRow)'s top — inside row \(Self.historyPromptRow), not row \(topRow)"
        )
    }

    /// Absolute content y of the vertical middle of the bottom-most buffer line.
    private func lastLineMidY(_ terminal: MobaideTerminalView) -> CGFloat {
        let lineCount = terminal.getTerminal().buffer.yDisp + terminal.getTerminal().getDims().rows
        return (CGFloat(lineCount - 1) + 0.5) * terminal.cellSize.height
    }

    /// The offset `scrollToBottom()` parks at is a sub-cell floor *below*
    /// SwiftTerm's own tail — `contentSize.height - bounds.height` against a
    /// viewport whose height is not a whole number of cells. Flooring it gave a
    /// window origin of `yDisp - 1`, and since `getLine(row:)` addresses exactly
    /// `rows` lines from that origin, the last buffer line — the live prompt —
    /// fell out of reach and every tap on it after a jump-to-bottom did nothing.
    func testResolveRowReachesTheLiveInputAtTheParkedTailOffset() {
        let terminal = scrolledBackTerminal()
        let cellHeight = terminal.cellSize.height
        let parked = terminal.maxScrollOffsetY
        XCTAssertNotEqual(
            parked.truncatingRemainder(dividingBy: cellHeight), 0, accuracy: 0.01,
            "the harness must park sub-cell-misaligned, or the regression cannot reproduce here"
        )
        terminal.contentOffset = CGPoint(x: 0, y: parked)

        let resolved = terminal.resolveRow(at: CGPoint(x: 20, y: lastLineMidY(terminal)))

        XCTAssertEqual(
            resolved?.text, "echo tail",
            "the window has to be raised to cover the tapped row, not left at the floored viewport top"
        )
        XCTAssertEqual(resolved?.isCurrentInput, true, "parked at the tail, the cursor row is the live input line")
    }

    // MARK: - Tap ownership ladder

    /// A recognizer that never saw a finger answers `.zero` for every location,
    /// so only the point is stubbed — the view it reports, the handler it
    /// reaches and the emulator it reads all stay real.
    private final class StubTapGesture: UITapGestureRecognizer {
        var point: CGPoint = .zero
        override func location(in view: UIView?) -> CGPoint { point }
    }

    /// Invokes a Coordinator tap handler through the very selector the
    /// `#selector` in `attachGestures` resolves to, so what runs is the shipped
    /// gate rather than a copy of it in the test.
    private func fire(
        _ selectorName: String,
        on coordinator: TerminalHostView.Coordinator,
        view: MobaideTerminalView,
        at point: CGPoint
    ) {
        let gesture = StubTapGesture()
        gesture.point = point
        view.addGestureRecognizer(gesture)
        let selector = NSSelectorFromString(selectorName)
        XCTAssertTrue(coordinator.responds(to: selector), "\(selectorName) is a recognizer action — renaming it unhooks the tap")
        _ = coordinator.perform(selector, with: gesture)
        view.removeGestureRecognizer(gesture)
    }

    /// One real tap. UIKit hands it to both single-tap recognizers — the click
    /// at once, the row tap when the double-tap window closes — so what the tap
    /// does is what the pair of them does together.
    private func tapTerminal(
        on coordinator: TerminalHostView.Coordinator,
        view: MobaideTerminalView,
        at point: CGPoint
    ) {
        fire("handleMouseTap:", on: coordinator, view: view, at: point)
        fire("handleSingleTap:", on: coordinator, view: view, at: point)
    }

    /// Every frame a tap can produce: the click and the CR ride `i`, an
    /// historical prompt rides `x`.
    private func tapFrames(_ connection: FakeWebSocketConnection) -> [Data] {
        connection.sentBinaries.filter { $0.first == 0x69 || $0.first == 0x78 }
    }

    private func tappableModel() async throws
        -> (TerminalSessionModel, FakeWebSocketConnection, TerminalHostView.Coordinator) {
        let (model, connection) = try await wiredModel()
        let coordinator = try XCTUnwrap(retainedCoordinator)
        coordinator.attachGestures(to: model.terminalView)
        return (model, connection, coordinator)
    }

    /// The live prompt row of whichever buffer is current, drawn at content row
    /// 0 with the cursor still on it — what Claude Code's Ink prompt looks like
    /// to the row resolver.
    private func promptRowPoint(_ terminal: MobaideTerminalView) -> CGPoint {
        terminal.feed(text: "> echo hi")
        return CGPoint(x: terminal.cellSize.width * 5.5, y: terminal.cellSize.height * 0.5)
    }

    func testMouseReportingTapClicksTheTappedCellAndNeverSubmitsTheRow() async throws {
        let (model, connection, coordinator) = try await tappableModel()
        let terminal = model.terminalView
        enableMouseReporting(terminal)
        let point = promptRowPoint(terminal)
        XCTAssertEqual(
            terminal.resolveRow(at: point)?.text, "echo hi",
            "the row must still resolve, or nothing but the gate could be keeping the CR off the wire"
        )

        tapTerminal(on: coordinator, view: terminal, at: point)

        let frames = await waitForInputFrames(connection, atLeast: 1)
        XCTAssertEqual(
            frames,
            [inputFrame("\u{1B}[<0;6;1M\u{1B}[<0;6;1m")],
            "left button (0, not SwiftTerm's middle-button 1) pressed and released on the tapped cell, "
                + "1-based on the wire, coalesced into the one write a single gesture deserves"
        )
        XCTAssertEqual(
            tapFrames(connection).count, 1,
            "the click is the whole tap — the CR on top of it is what submitted the half-typed message"
        )
    }

    func testPlainShellTapStillSubmitsTheLiveInput() async throws {
        let (model, connection, coordinator) = try await tappableModel()
        let terminal = model.terminalView
        let point = promptRowPoint(terminal)

        tapTerminal(on: coordinator, view: terminal, at: point)

        let frames = await waitForInputFrames(connection, atLeast: 1)
        XCTAssertEqual(
            frames, [inputFrame("\r")],
            "the untouched arm: on the normal buffer the app owns the pointer and a tap re-runs the row"
        )
    }

    func testAttachGesturesKeepsExactlyOneLiveMouseTapPerVisit() {
        let model = makeModel()
        let terminal = model.terminalView
        TerminalHostView.Coordinator(model: model).attachGestures(to: terminal)
        let first = recognizers(named: "mobaide.mouseTap", on: terminal)

        TerminalHostView.Coordinator(model: model).attachGestures(to: terminal)

        let second = recognizers(named: "mobaide.mouseTap", on: terminal)
        XCTAssertEqual(first.count, 1)
        XCTAssertEqual(second.count, 1, "a second visit must replace the recognizer, not report two clicks per tap")
        XCTAssertFalse(second[0] === first[0], "it targets the Coordinator, so a stale one would click into the previous visit")
        XCTAssertEqual((second[0] as? UITapGestureRecognizer)?.numberOfTapsRequired, 1)
        XCTAssertTrue(
            second[0].isEnabled,
            "attachGestures ends in neutralizeSwiftTermGestures, which disables every tap not named mobaide.*"
        )
    }

    // MARK: - Synchronized output

    /// Emulator output as the app really receives it: an `o` frame, so
    /// `handleBinary` routes the bytes through `feedTrackingSynchronizedOutput`.
    /// The priming helpers deliberately use `feed(text:)`, which bypasses the
    /// scanner — a hand-set flag would prove nothing about the shipped path.
    private func deliver(_ text: String, to connection: FakeWebSocketConnection) {
        connection.emit(.binary(outputFrame(text)))
    }

    private func freeze(
        _ connection: FakeWebSocketConnection, _ terminal: MobaideTerminalView
    ) async -> Bool {
        deliver("\u{1B}[?2026h", to: connection)
        return await poll { terminal.isSynchronizedOutputActive }
    }

    private func thaw(
        _ connection: FakeWebSocketConnection, _ terminal: MobaideTerminalView
    ) async -> Bool {
        deliver("\u{1B}[?2026l", to: connection)
        return await poll { !terminal.isSynchronizedOutputActive }
    }

    /// One tap whose click lands while the display is frozen but whose row gesture
    /// only runs after the program has released the freeze. `require(toFail:)` holds
    /// the row gesture for the whole double-tap interval, and that gap is all the room
    /// a program needs to drop mode 2026 — leaving the row it swapped in both drawn
    /// and resolvable by the time the gate runs. Returns whether the release landed.
    private func tapReleasingTheFreezeBeforeTheRowGesture(
        _ rowGesture: String,
        on coordinator: TerminalHostView.Coordinator,
        view: MobaideTerminalView,
        at point: CGPoint,
        through connection: FakeWebSocketConnection
    ) async -> Bool {
        fire("handleMouseTap:", on: coordinator, view: view, at: point)
        let thawed = await thaw(connection, view)
        fire(rowGesture, on: coordinator, view: view, at: point)
        return thawed
    }

    func testTapWhileTheDisplayIsFrozenResolvesNothing() async throws {
        let (model, connection, coordinator) = try await tappableModel()
        let terminal = model.terminalView
        let point = promptRowPoint(terminal)
        XCTAssertEqual(
            terminal.resolveRow(at: point)?.text, "echo hi",
            "the row must resolve before the freeze, or nothing but the gate could be keeping it off the wire"
        )
        let frozen = await freeze(connection, terminal)
        XCTAssertTrue(frozen, "real DECSET 2026 bytes have to reach the scanner through handleBinary")

        tapTerminal(on: coordinator, view: terminal, at: point)

        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            tapFrames(connection).isEmpty,
            "SwiftTerm has stopped painting while it keeps parsing, so the tapped line is not the drawn "
                + "one — an `x` execute frame here runs text the user never saw"
        )
        XCTAssertNil(
            terminal.resolveRow(at: point),
            "the refusal belongs in resolveRow, which double-tap-to-composer shares — spoofed text in "
                + "the composer is the first half of the same attack"
        )
    }

    /// The freeze is released before the finger goes down, which is the whole of what
    /// "the gate reopened" can mean once the reading is latched at lift — a release
    /// that lands mid-tap instead is
    /// [testFreezeReleasedInsideTheTapGapStillResolvesNothing].
    func testTapSubmitsAgainOnceTheProgramEndsSynchronizedOutput() async throws {
        let (model, connection, coordinator) = try await tappableModel()
        let terminal = model.terminalView
        let point = promptRowPoint(terminal)
        let frozen = await freeze(connection, terminal)
        XCTAssertTrue(frozen)

        let thawed = await thaw(connection, terminal)
        XCTAssertTrue(thawed, "Ink brackets every frame, so a window outliving its own `l` kills the affordance")
        tapTerminal(on: coordinator, view: terminal, at: point)

        let frames = await waitForInputFrames(connection, atLeast: 1)
        XCTAssertEqual(frames, [inputFrame("\r")], "the untouched arm has to come back exactly as it was")
    }

    /// The bypass: the click recognizes at lift, the row tap only a double-tap timeout
    /// later, and a program that releases the freeze in between leaves `resolveRow`'s
    /// own check with nothing to refuse on. Control:
    /// [testPlainShellTapStillSubmitsTheLiveInput], the same tap with no freeze at all.
    func testFreezeReleasedInsideTheTapGapStillResolvesNothing() async throws {
        let (model, connection, coordinator) = try await tappableModel()
        let terminal = model.terminalView
        let point = promptRowPoint(terminal)
        let frozen = await freeze(connection, terminal)
        XCTAssertTrue(frozen, "the finger has to lift while the snapshot is still what the user sees")

        let thawed = await tapReleasingTheFreezeBeforeTheRowGesture(
            "handleSingleTap:", on: coordinator, view: terminal, at: point, through: connection
        )

        XCTAssertTrue(thawed, "the interleaving is the whole test: the row tap must run with the live check clear")
        XCTAssertEqual(
            terminal.resolveRow(at: point)?.text, "echo hi",
            "and with the row resolving again, so only the reading latched at lift can be refusing"
        )
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            tapFrames(connection).isEmpty,
            "what the user decided against is the screen at lift — a row swapped in behind the "
                + "snapshot is not a row anyone tapped"
        )
    }

    /// Control for [testDoubleTapAfterAFreezeReleasedInsideTheTapGapCopiesNothing].
    func testDoubleTapCopiesTheTappedRowToTheComposer() async throws {
        let (model, _, coordinator) = try await tappableModel()
        let terminal = model.terminalView
        let point = promptRowPoint(terminal)

        fire("handleMouseTap:", on: coordinator, view: terminal, at: point)
        fire("handleDoubleTap:", on: coordinator, view: terminal, at: point)

        XCTAssertEqual(
            model.inputText, "echo hi",
            "the untouched arm: copy-to-composer is the non-destructive way to re-run a message"
        )
    }

    /// [testFreezeReleasedInsideTheTapGapStillResolvesNothing] for the other gesture the
    /// latch guards — a double tap is preceded by the same prompt click, so the same
    /// mid-tap release leaves it reading a row the user never saw.
    func testDoubleTapAfterAFreezeReleasedInsideTheTapGapCopiesNothing() async throws {
        let (model, connection, coordinator) = try await tappableModel()
        let terminal = model.terminalView
        let point = promptRowPoint(terminal)
        let frozen = await freeze(connection, terminal)
        XCTAssertTrue(frozen)

        let thawed = await tapReleasingTheFreezeBeforeTheRowGesture(
            "handleDoubleTap:", on: coordinator, view: terminal, at: point, through: connection
        )

        XCTAssertTrue(thawed, "the release has to land before the gesture, or resolveRow would refuse on its own")
        XCTAssertEqual(
            model.inputText, "",
            "spoofed text in the composer is the first half of the same attack, so it fails closed "
                + "on the same latched reading"
        )
    }
}
