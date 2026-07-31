import UIKit
import XCTest
@testable import Mobaide

@MainActor
final class TerminalHostGestureTests: XCTestCase {
    private final class PanSubclass: UIPanGestureRecognizer {}

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

    func testExactClassCheckSparesPanSubclasses() {
        let terminal = makeTerminal()
        let subclassPan = PanSubclass()
        terminal.addGestureRecognizer(subclassPan)

        terminal.neutralizeSwiftTermGestures()

        XCTAssertTrue(
            subclassPan.isEnabled,
            "only exactly UIPanGestureRecognizer is neutralized — subclasses like UIScrollViewPanGestureRecognizer must survive"
        )
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

    // MARK: - Coordinator.attachGestures

    func testAttachGesturesReplacesStaleRecognizersFromPreviousVisit() {
        let model = makeModel()
        let terminal = model.terminalView

        let firstCoordinator = TerminalHostView.Coordinator(model: model)
        firstCoordinator.attachGestures(to: terminal)
        let firstTap = recognizers(named: "mobaide.rowTap", on: terminal).first
        XCTAssertNotNil(firstTap)

        let secondCoordinator = TerminalHostView.Coordinator(model: model)
        secondCoordinator.attachGestures(to: terminal)

        let taps = recognizers(named: "mobaide.rowTap", on: terminal)
        let doubleTaps = recognizers(named: "mobaide.rowDoubleTap", on: terminal)
        XCTAssertEqual(taps.count, 1, "re-attaching must not accumulate duplicates")
        XCTAssertEqual(doubleTaps.count, 1)
        XCTAssertFalse(
            taps[0] === firstTap,
            "the recognizer must be rebuilt so it targets the current Coordinator, not the first visit's"
        )
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

    func testAlternateScreenWithoutMouseReportingAlsoTakesTheAxis() {
        let terminal = makeTerminal()
        terminal.configureAppearance()
        // `less`/`vim`: no mouse mode change ever fires, so a stored flag would
        // never cover this — the mode has to be re-derived per touch.
        enterAlternateScreen(terminal)

        XCTAssertEqual(terminal.wheelScrollMode, .cursorKeys)
        XCTAssertFalse(terminal.gestureRecognizerShouldBegin(terminal.panGestureRecognizer))
    }

    func testMouseReportingOnTheNormalBufferTakesTheAxis() {
        let terminal = makeTerminal()
        terminal.configureAppearance()
        enableMouseReporting(terminal)

        XCTAssertEqual(terminal.wheelScrollMode, .wheel)
        XCTAssertFalse(terminal.gestureRecognizerShouldBegin(terminal.panGestureRecognizer))
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

    // MARK: - Vertical-scroll recognizer lifecycle

    func testConfigureAppearanceInstallsExactlyOneVerticalScrollGesture() {
        let terminal = makeTerminal()

        terminal.configureAppearance()
        let installed = recognizers(named: MobaideTerminalView.verticalScrollGestureName, on: terminal)
        terminal.installVerticalScrollGesture()

        XCTAssertEqual(installed.count, 1)
        let afterRepeat = recognizers(named: MobaideTerminalView.verticalScrollGestureName, on: terminal)
        XCTAssertEqual(afterRepeat.count, 1, "installation must be idempotent")
        XCTAssertTrue(afterRepeat[0] === installed[0])
    }

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

    func testWheelModeSendsOneReportPerRowPinnedToTheTouchCell() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)
        let anchor = CGPoint(x: terminal.cellSize.width * 5.5, y: terminal.cellSize.height * 3.5)

        terminal.emitVerticalScroll(rowsDown: 3, at: anchor)

        let frames = await waitForInputFrames(connection, atLeast: 3)
        let expected = inputFrame("\u{1B}[<65;6;4M")
        XCTAssertEqual(
            frames, [expected, expected, expected],
            "three rows are three wheel-down reports, every one at the cell the drag began on"
        )
    }

    func testUpwardScrollUsesTheWheelUpButton() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)

        terminal.emitVerticalScroll(rowsDown: -1, at: .zero)

        let frames = await waitForInputFrames(connection, atLeast: 1)
        XCTAssertEqual(frames, [inputFrame("\u{1B}[<64;1;1M")], "coordinates are 1-based on the wire")
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

    func testNormalBufferEmitsNothing() async throws {
        let (model, connection) = try await wiredModel()

        model.terminalView.emitVerticalScroll(rowsDown: 5, at: .zero)

        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            inputFrames(connection).isEmpty,
            "a plain shell keeps the untouched SwiftTerm scrollback path — nothing goes to the PTY"
        )
    }

    func testOneCallbackNeverEmitsMoreThanOnePage() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)
        let rows = terminal.getTerminal().getDims().rows
        XCTAssertGreaterThan(rows, 3, "the cap is only meaningful against a real page of rows")

        terminal.emitVerticalScroll(rowsDown: 1000, at: .zero)

        let frames = await waitForInputFrames(connection, atLeast: rows)
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(
            inputFrames(connection).count, rows,
            "a stalled main thread delivers one huge delta; each report costs a round trip plus a full repaint"
        )
        XCTAssertEqual(frames.count, rows)
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
}
