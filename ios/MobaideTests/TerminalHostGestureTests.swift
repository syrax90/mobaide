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

    // MARK: - Horizontal-axis handoff

    func testScrollbackPanYieldsExactlyTheDragsTheOuterClaims() {
        XCTAssertTrue(
            MobaideTerminalView.allowsScrollbackPan(mode: .localScrollback, outerClaimsHorizontalPan: false),
            "the outer claims iff dx > dy, so every remaining direction — diagonal ties included — is the scrollback's"
        )
        XCTAssertFalse(
            MobaideTerminalView.allowsScrollbackPan(mode: .localScrollback, outerClaimsHorizontalPan: true),
            "the nested pans recognize simultaneously; without this the outer's horizontal drag would jiggle the scrollback too"
        )
    }

    func testWheelLadderModesRefuseTheScrollbackPanWhicheverWayTheOuterDecides() {
        for mode in [TerminalWheelScroll.Mode.wheel, .cursorKeys] {
            for outerClaims in [true, false] {
                XCTAssertFalse(
                    MobaideTerminalView.allowsScrollbackPan(mode: mode, outerClaimsHorizontalPan: outerClaims),
                    "\(mode) owns the vertical axis whatever the horizontal split decides (outer claims: \(outerClaims))"
                )
            }
        }
    }

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

    func testFullModeHostLeavesTheScrollbackPanToTheTerminal() {
        let (scroll, terminal) = hostedTerminal(mode: .full)

        XCTAssertTrue(terminal.superview === scroll, "the host is resolved per touch from the live hierarchy")
        XCTAssertTrue(
            terminal.gestureRecognizerShouldBegin(terminal.panGestureRecognizer),
            "Full mode never arbitrates, so a drag starting with horizontal drift must still reach the scrollback"
        )
    }

    func testStaticModeHostLeavesAnUnclaimedDragToTheTerminal() {
        let (scroll, terminal) = hostedTerminal(mode: .static)

        // Binding the host matters: nothing else retains it, and a released
        // wrapper would null `superview` and route this through the no-host
        // fallback instead of the Static-mode claim it means to exercise.
        XCTAssertTrue(terminal.superview === scroll, "the host is resolved per touch from the live hierarchy")
        XCTAssertTrue(
            terminal.gestureRecognizerShouldBegin(terminal.panGestureRecognizer),
            "the outer claims only horizontally dominant drags; every other one stays the scrollback's"
        )
    }

    func testHostedTerminalOnTheAlternateBufferStillRefusesTheScrollbackPan() {
        let (scroll, terminal) = hostedTerminal(mode: .full)
        enterAlternateScreen(terminal)

        XCTAssertTrue(terminal.superview === scroll, "the host is resolved per touch from the live hierarchy")
        XCTAssertFalse(
            terminal.gestureRecognizerShouldBegin(terminal.panGestureRecognizer),
            "the horizontal split is an extra refusal, never a licence — the wheel ladder still owns the axis"
        )
    }

    /// The whole join — host lookup, display mode, wheel-ladder arm and the
    /// `outerClaimsHorizontalPan:` polarity — answered on a hosted terminal for
    /// a drag the test supplies. The recognizer path cannot be driven this way:
    /// `UIScrollViewPanGestureRecognizer` refuses a planted `setTranslation`
    /// and `velocity(in:)` is unsettable, so the sampled overload is where the
    /// arbitration becomes observable outside a device.
    func testStaticHostRefusesTheScrollbackPanOnAHorizontallyDominantDrag() {
        let (scroll, terminal) = hostedTerminal(mode: .static)

        XCTAssertTrue(terminal.superview === scroll, "the host is resolved per touch from the live hierarchy")
        XCTAssertFalse(
            terminal.allowsScrollbackPan(velocity: Self.horizontallyDominantDrag, translation: .zero),
            "the nested pans recognize simultaneously, so a drag the outer pans sideways must not also jiggle the scrollback"
        )
    }

    func testStaticHostKeepsTheScrollbackPanForAVerticallyDominantDrag() {
        let (scroll, terminal) = hostedTerminal(mode: .static)

        XCTAssertTrue(terminal.superview === scroll, "the host is resolved per touch from the live hierarchy")
        XCTAssertTrue(
            terminal.allowsScrollbackPan(velocity: Self.verticallyDominantDrag, translation: .zero),
            "sideways drift on a vertical drag is not a claim — the touch stays the scrollback's"
        )
    }

    func testStaticHostGivesAnExactDiagonalTieToTheScrollback() {
        let (scroll, terminal) = hostedTerminal(mode: .static)

        XCTAssertTrue(terminal.superview === scroll, "the host is resolved per touch from the live hierarchy")
        XCTAssertTrue(
            terminal.allowsScrollbackPan(velocity: CGPoint(x: 70, y: 70), translation: .zero),
            "Android parity: the outer claims iff dx > dy, so the tie is the scrollback's"
        )
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

    func testFlingTravelStaysWithinHalfAPage() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)
        let rows = terminal.getTerminal().getDims().rows

        await flungTerminal(terminal)

        XCTAssertEqual(
            wheelReportCount(in: inputFrames(connection)), rows / 2,
            "Android's clamp, and the cost ceiling for one flick: each row is a round trip plus a full repaint"
        )
    }

    func testFlingDecelerates() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)

        await flungTerminal(terminal)

        let perFrame = inputFrames(connection).map { wheelReportCount(in: $0) }
        XCTAssertGreaterThan(perFrame.count, 2)
        XCTAssertGreaterThan(
            perFrame[0], perFrame[perFrame.count - 1],
            "a fling that keeps its pace to the last frame reads as a jump, not momentum"
        )
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

    func testLeavingTheScreenCancelsAnInFlightFling() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)

        let caught = await interruptedFling(terminal, connection) { $0.willMove(toWindow: nil) }

        try await Task.sleep(for: TerminalWheelScroll.flingFrameInterval * 6)
        XCTAssertEqual(
            wheelReportCount(in: inputFrames(connection)), caught,
            "the terminal view outlives the screen, so a fling left running would write to a session the user left"
        )
        XCTAssertLessThan(caught, terminal.getTerminal().getDims().rows / 2, "the fling must have been cut short")
    }

    func testSlowLiftDoesNotFling() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal)
        enableMouseReporting(terminal)

        terminal.startFling(velocity: CGPoint(x: 0, y: -200))
        await terminal.flingTask?.value

        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(inputFrames(connection).isEmpty, "a drag that ends slowly stops where the finger left it")
    }

    func testNormalBufferNeverFlings() async throws {
        let (model, connection) = try await wiredModel()
        let terminal = model.terminalView

        terminal.startFling(velocity: hardFlickUp)
        await terminal.flingTask?.value

        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            inputFrames(connection).isEmpty,
            "a plain shell keeps SwiftTerm's own scroll view, which already decelerates by itself"
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

    func testConfigureAppearanceClaimsTheScrollViewDelegate() {
        let terminal = makeTerminal()
        XCTAssertNil(
            terminal.delegate,
            "SwiftTerm declares UIScrollViewDelegate conformance but implements no method of it and never claims the slot"
        )

        terminal.configureAppearance()

        XCTAssertNotNil(terminal.delegate, "a drag reaches the app through the scroll view or not at all")
        XCTAssertFalse(
            terminal.delegate === terminal,
            "answering the protocol on the view itself would silently adopt SwiftTerm's empty conformance"
        )
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

    func testNonUserDrivenScrollReportIsDropped() {
        let model = scrollableModel()
        let terminal = model.terminalView
        terminal.contentOffset = CGPoint(x: 0, y: terminal.maxScrollOffsetY / 2)

        terminal.handleScrollViewDidScroll(isUserDriven: false)

        XCTAssertTrue(
            model.followMode,
            "only a touch may pause follow mode — every other offset write is the app's or SwiftTerm's own"
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

    func testFollowingViewportIsStillPulledToTheTailByOutput() {
        let model = scrollableModel()
        let terminal = model.terminalView
        terminal.contentOffset = .zero
        XCTAssertTrue(model.followMode)
        XCTAssertFalse(terminal.isScrolledToBottom, "the offset has to start away from the tail for the snap to show")

        terminal.feed(text: "fresh output\r\n")

        XCTAssertTrue(
            terminal.isScrolledToBottom,
            "suppression is scoped to a paused viewport — following output must keep working"
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
            "scroll(toPosition: 1.0) is guarded by `row != yDisp` and yDisp is already at the tail, so the button did nothing"
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
        // `yDisp` is pinned to `yBase`, so its delta counts the lines the
        // emulator actually pushed into the scrollback.
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

    func testResolveRowAtTheTailIsTheCurrentInput() {
        let terminal = scrolledBackTerminal()
        let cellHeight = terminal.cellSize.height
        let lastRow = terminal.getTerminal().getDims().rows - 1

        let resolved = terminal.resolveRow(
            at: CGPoint(x: 20, y: terminal.contentOffset.y + CGFloat(lastRow) * cellHeight + cellHeight / 2)
        )

        XCTAssertEqual(resolved?.text, "echo tail")
        XCTAssertEqual(resolved?.isCurrentInput, true, "the cursor row at the tail is the live input line")
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

    func testOverscrolledTapOnTheLastLineStillResolves() {
        let terminal = scrolledBackTerminal()
        let cellHeight = terminal.cellSize.height
        let yDispBefore = terminal.getTerminal().buffer.yDisp
        // A small overscroll past `maxScrollOffsetY` — but the tap below
        // targets a point that is still genuinely inside the rendered
        // content, so the window still has to clamp onto the last real line.
        terminal.contentOffset = CGPoint(x: 0, y: terminal.maxScrollOffsetY + 2 * cellHeight)
        let lineCount = terminal.getTerminal().buffer.yDisp + terminal.getTerminal().getDims().rows

        let resolved = terminal.resolveRow(
            at: CGPoint(x: 20, y: (CGFloat(lineCount - 1) + 0.5) * cellHeight)
        )

        XCTAssertEqual(
            resolved?.text, "echo tail",
            "the read window still clamps to the last real line when the tap is genuinely inside it"
        )
        XCTAssertEqual(
            terminal.getTerminal().buffer.yDisp, yDispBefore,
            "the re-pointed read window must be restored before any SwiftTerm code observes it"
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

    func testResolveRowReachesTheLiveInputAfterAJumpToBottom() {
        let model = scrollableModel()
        let terminal = model.terminalView
        scrollUp(terminal)

        model.jumpToBottom()

        let resolved = terminal.resolveRow(at: CGPoint(x: 20, y: lastLineMidY(terminal)))
        XCTAssertEqual(resolved?.text, "echo tail", "the prompt must stay tappable after the button returns to the tail")
        XCTAssertEqual(resolved?.isCurrentInput, true)
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

    func testBareAlternateScreenTapNeitherClicksNorSubmits() async throws {
        let (model, connection, coordinator) = try await tappableModel()
        let terminal = model.terminalView
        enterAlternateScreen(terminal) // `less`, `man`, plain vim: no mouse reporting
        let point = promptRowPoint(terminal)
        XCTAssertEqual(terminal.resolveRow(at: point)?.text, "echo hi", "the row resolves; only the gate stops the tap")

        tapTerminal(on: coordinator, view: terminal, at: point)

        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            tapFrames(connection).isEmpty,
            "the rows are the program's own chrome — submitting one Ctrl+Cs it, and it asked for no clicks"
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

    func testX10OnlyProgramGetsNeitherAClickNorASubmit() async throws {
        let (model, connection, coordinator) = try await tappableModel()
        let terminal = model.terminalView
        terminal.feed(text: "\u{1B}[?9h") // DECSET 9 — X10 press-only reporting
        let point = promptRowPoint(terminal)

        tapTerminal(on: coordinator, view: terminal, at: point)

        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            tapFrames(connection).isEmpty,
            "SwiftTerm suppresses the press in X10 itself, so mirroring its rule is what keeps a half-report off the wire"
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

    /// Stand-in for `ProcessInfo.systemUptime`. A reference type, so the closure
    /// the view keeps reads whatever the test has moved it to.
    private final class StepClock {
        var now: TimeInterval = 1_000
    }

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
            "SwiftTerm is drawing a deep copy the app cannot read, so the tapped line is not the drawn "
                + "one — an `x` execute frame here runs text the user never saw"
        )
        XCTAssertNil(
            terminal.resolveRow(at: point),
            "the refusal belongs in resolveRow, which double-tap-to-composer shares — spoofed text in "
                + "the composer is the first half of the same attack"
        )
    }

    func testTapSubmitsAgainOnceTheProgramEndsSynchronizedOutput() async throws {
        let (model, connection, coordinator) = try await tappableModel()
        let terminal = model.terminalView
        let point = promptRowPoint(terminal)
        let frozen = await freeze(connection, terminal)
        XCTAssertTrue(frozen)

        deliver("\u{1B}[?2026l", to: connection)
        let thawed = await poll { !terminal.isSynchronizedOutputActive }
        XCTAssertTrue(thawed, "Ink brackets every frame, so a window outliving its own `l` kills the affordance")
        tapTerminal(on: coordinator, view: terminal, at: point)

        let frames = await waitForInputFrames(connection, atLeast: 1)
        XCTAssertEqual(frames, [inputFrame("\r")], "the untouched arm has to come back exactly as it was")
    }

    func testTapSubmitsAgainOnceTheFreezeDeadlinePasses() async throws {
        let (model, connection, coordinator) = try await tappableModel()
        let terminal = model.terminalView
        let clock = StepClock()
        terminal.synchronizedOutputNow = { clock.now }
        let point = promptRowPoint(terminal)
        // The program arms the mode and never resets it — the shape that would
        // otherwise disable row taps for as long as the session lives.
        let frozen = await freeze(connection, terminal)
        XCTAssertTrue(frozen)

        clock.now += TerminalSynchronizedOutput.emulatorTimeout + TerminalSynchronizedOutput.expiryGrace
        tapTerminal(on: coordinator, view: terminal, at: point)

        let frames = await waitForInputFrames(connection, atLeast: 1)
        XCTAssertEqual(
            frames, [inputFrame("\r")],
            "the gate expires with the emulator's own snapshot, so holding the mode set cannot deaden "
                + "the affordance for good"
        )
    }
}
