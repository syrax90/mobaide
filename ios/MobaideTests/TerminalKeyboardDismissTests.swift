import UIKit
import XCTest
@testable import Mobaide

/// Covers touching the terminal while a docked software keyboard is up: the touch
/// puts the keyboard away — whichever path raised it — and is spent, so it must not
/// also resend a prompt row, click into a mouse-tracking TUI or copy to the
/// composer. A drag still scrolls and a live selection still owns the pointer.
@MainActor
final class TerminalKeyboardDismissTests: XCTestCase {
    /// The Coordinator's callbacks hold it weakly (view → closure →
    /// Coordinator), so the wiring dies with the local unless it is retained.
    private var retainedCoordinator: TerminalHostView.Coordinator?

    /// A terminal mounted in the wrapper that owns the keyboard overlap, with
    /// the Coordinator's real recognizers attached: the overlap is read through
    /// the live hierarchy, so an unhosted view answers a different question.
    private func hostedTerminal(
        transportFactory: FakeWebSocketTransportFactory = FakeWebSocketTransportFactory()
    ) -> (TerminalSessionModel, MobaideTerminalView, TerminalPanScrollView) {
        let model = TerminalTestSupport.model(transportFactory: transportFactory)
        let terminal = model.terminalView
        terminal.configureAppearance()
        let scroll = TerminalPanScrollView(
            terminalView: terminal,
            displayConfig: TerminalDisplayConfig(mode: .full, staticColumns: 120)
        )
        scroll.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        scroll.layoutIfNeeded()
        let coordinator = TerminalHostView.Coordinator(model: model)
        retainedCoordinator = coordinator
        coordinator.attachGestures(to: terminal)
        return (model, terminal, scroll)
    }

    /// Mounts `view` in a key window and tears both down even on assert
    /// failure, so no test leaks first-responder state into the next.
    private func hostInKeyWindow(_ view: UIView) {
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        let root = UIViewController()
        window.rootViewController = root
        view.frame = root.view.bounds
        root.view.addSubview(view)
        window.makeKeyAndVisible()
        addTeardownBlock { @MainActor in
            _ = view.resignFirstResponder()
            window.isHidden = true
        }
    }

    private func rowTap(on terminal: MobaideTerminalView) throws -> UIGestureRecognizer {
        try XCTUnwrap((terminal.gestureRecognizers ?? []).first { $0.name == "mobaide.rowTap" })
    }

    private func raiseKeyboard(_ scroll: TerminalPanScrollView) {
        scroll.setKeyboardLift(300, duration: 0)
    }

    private func hideKeyboard(_ scroll: TerminalPanScrollView) {
        scroll.setKeyboardLift(0, duration: 0)
    }

    // MARK: - The touch-down decision

    func testTouchWhileTheKeyboardIsUpDismissesItAndSpendsTheTap() throws {
        let (_, terminal, scroll) = hostedTerminal()
        var dismissals = 0
        terminal.onDismissSoftKeyboard = { dismissals += 1 }
        raiseKeyboard(scroll)
        XCTAssertTrue(terminal.isSoftKeyboardCovering)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertEqual(dismissals, 1)
        XCTAssertTrue(terminal.dismissedKeyboardOnCurrentTouch)
        let tap = try rowTap(on: terminal)
        XCTAssertFalse(
            terminal.gestureRecognizerShouldBegin(tap),
            "a tap meant only to hide the keyboard must never also resend the row under it (the PR #483 bug class)"
        )
    }

    func testTouchWithTheKeyboardDownIsLeftAlone() throws {
        let (_, terminal, _) = hostedTerminal()
        var dismissals = 0
        terminal.onDismissSoftKeyboard = { dismissals += 1 }

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertFalse(
            terminal.isSoftKeyboardCovering,
            "lift 0 also covers the undocked/floating keyboards, which overlap nothing. A hardware "
                + "keyboard is not this case — it reports the small non-zero lift of its assistant bar, "
                + "and is covered by the threshold tests below"
        )
        XCTAssertEqual(dismissals, 0)
        XCTAssertFalse(terminal.dismissedKeyboardOnCurrentTouch)
        let tap = try rowTap(on: terminal)
        XCTAssertTrue(
            terminal.gestureRecognizerShouldBegin(tap),
            "with the keyboard down every existing tap behavior has to be unchanged"
        )
    }

    func testTouchWithNoKeyboardHostSpendsNothing() throws {
        let model = TerminalTestSupport.model()
        let terminal = model.terminalView
        terminal.configureAppearance()
        let coordinator = TerminalHostView.Coordinator(model: model)
        retainedCoordinator = coordinator
        coordinator.attachGestures(to: terminal)
        var dismissals = 0
        terminal.onDismissSoftKeyboard = { dismissals += 1 }

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertFalse(
            terminal.isSoftKeyboardCovering,
            "a view SwiftUI has re-parented away from its wrapper has no overlap to read — defaulting the other way would swallow every tap"
        )
        XCTAssertEqual(dismissals, 0)
        let tap = try rowTap(on: terminal)
        XCTAssertTrue(terminal.gestureRecognizerShouldBegin(tap))
    }

    /// The whole reason the answer is latched rather than re-read: `mobaide.rowTap`
    /// waits out the double-tap timeout, so `keyboardWillHide` has already zeroed
    /// the lift by the time UIKit asks whether it may begin.
    func testLatchOutlivesTheKeyboardGoingDownBeforeTheTapRecognizes() throws {
        let (_, terminal, scroll) = hostedTerminal()
        raiseKeyboard(scroll)
        terminal.touchesBegan(Set<UITouch>(), with: nil)

        hideKeyboard(scroll)

        XCTAssertFalse(
            terminal.isSoftKeyboardCovering,
            "the live answer has flipped — reading it here is what would let the tap through and send a bare CR"
        )
        let tap = try rowTap(on: terminal)
        XCTAssertFalse(terminal.gestureRecognizerShouldBegin(tap))
    }

    func testTheNextTouchWithTheKeyboardDownClearsTheLatch() throws {
        let (_, terminal, scroll) = hostedTerminal()
        var dismissals = 0
        terminal.onDismissSoftKeyboard = { dismissals += 1 }
        raiseKeyboard(scroll)
        terminal.touchesBegan(Set<UITouch>(), with: nil)
        hideKeyboard(scroll)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertFalse(
            terminal.dismissedKeyboardOnCurrentTouch,
            "one touch is spent, not the screen — recomputing at touch-down is the only thing that ever clears this"
        )
        XCTAssertEqual(dismissals, 1, "the second touch has no keyboard left to put away")
        let tap = try rowTap(on: terminal)
        XCTAssertTrue(
            terminal.gestureRecognizerShouldBegin(tap),
            "the tap after the dismissing one has to act again — one extra tap, not a dead surface"
        )
    }

    // MARK: - What the spent touch must not take away

    func testTheDismissingTouchStillScrolls() {
        let (_, terminal, scroll) = hostedTerminal()
        feedTranscript(terminal)
        XCTAssertGreaterThan(terminal.maxScrollOffsetY, 0, "the harness must have real scrollback for the pan to be worth beginning")
        raiseKeyboard(scroll)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertTrue(terminal.dismissedKeyboardOnCurrentTouch)
        XCTAssertTrue(
            terminal.gestureRecognizerShouldBegin(terminal.panGestureRecognizer),
            "only taps are spent: the UIScrollView `.onDrag` convention is that the drag both dismisses and scrolls"
        )
    }

    /// Arbitration alone is not the whole answer here: the selection's own dismiss
    /// tap is an enabled single-tap recognizer, so a sweep that ran anyway would
    /// cancel the very tap that ends the episode — leaving the selection and its
    /// floating toolbar stranded.
    func testALiveSelectionStillOwnsThePointerAfterADismissingTouch() throws {
        let (_, terminal, scroll) = hostedTerminal()
        var dismissals = 0
        terminal.onDismissSoftKeyboard = { dismissals += 1 }
        terminal.feed(text: "hello world\r\n")
        let cell = terminal.cellSize
        XCTAssertTrue(
            terminal.selectionController.beginSelection(at: CGPoint(x: cell.width * 1.5, y: cell.height * 0.5)),
            "there has to be a live selection for the arbitration order to mean anything"
        )
        // Same class and tap count as `mobaide.selectionDismissTap`, which the
        // controller installs enabled — the real one cannot report whether it
        // was written to.
        let selectionProbe = RecordingTapGesture()
        selectionProbe.name = "mobaide.selectionDismissTapProbe"
        terminal.addGestureRecognizer(selectionProbe)
        raiseKeyboard(scroll)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertEqual(
            selectionProbe.enabledWrites, [],
            "the episode already owns every tap, so there is nothing to spend — and cancelling would strand the toolbar"
        )
        XCTAssertEqual(dismissals, 1, "only the spending is skipped: the keyboard covering the selection still goes away")
        let dismissTap = try XCTUnwrap((terminal.gestureRecognizers ?? []).first {
            $0.name == TerminalSelectionController.dismissTapGestureName
        })
        XCTAssertTrue(terminal.dismissedKeyboardOnCurrentTouch)
        XCTAssertTrue(
            terminal.gestureRecognizerShouldBegin(dismissTap),
            "the selection branch runs first: swallowing the tap that ends a selection would strand its toolbar on screen"
        )
    }

    // MARK: - The join to the model

    func testTouchOnTheTerminalPutsTheRaisedKeyboardAwayThroughTheModel() {
        let (model, terminal, scroll) = hostedTerminal()
        hostInKeyWindow(scroll)
        model.showTerminalKeyboard()
        XCTAssertTrue(model.terminalKeyboardActive, "the harness must have a keyboard up before the touch can put one away")
        raiseKeyboard(scroll)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertFalse(
            model.terminalKeyboardActive,
            "the Coordinator wire is the only thing joining the touch to the model — without it the touch is latched and inert"
        )
        XCTAssertFalse(terminal.isFirstResponder)
    }

    /// The composer-raised path, which is the whole reason the resign is guarded:
    /// `resignFirstResponder()` answers true even when the receiver never held
    /// first responder, and SwiftTerm's override takes that as a focus change and
    /// reports it to the program.
    func testATerminalTouchWhileTheComposerHoldsTheKeyboardWritesNothingToThePty() async throws {
        let factory = FakeWebSocketTransportFactory()
        let (model, terminal, scroll) = hostedTerminal(transportFactory: factory)
        // `hostedTerminal` attaches the recognizers only; SwiftTerm holds
        // `terminalDelegate` weakly, so the byte path terminal → Coordinator →
        // model is closed here exactly as `makeUIView` does.
        terminal.terminalDelegate = try XCTUnwrap(retainedCoordinator)
        let connection = try await liveConnection(model, factory)
        terminal.feed(text: "\u{1b}[?1004h")
        let armed = await waitForInputFrames(connection, atLeast: 1)
        XCTAssertEqual(
            armed, [inputFrame("\u{1b}[I")],
            "enabling focus reporting answers with the current state — one frame that proves both that the "
                + "program is watching focus and that the wire carries what the emulator writes"
        )
        model.setInput("half-typed prompt", requestFocus: false)
        XCTAssertFalse(terminal.isFirstResponder, "the composer, not the terminal, is holding this keyboard up")
        let focusEvents = StreamCollector(model.focusRequests())
        raiseKeyboard(scroll)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        let focused = try await focusEvents.next()
        XCTAssertFalse(
            focused,
            "the composer field is released, so the touch did reach the model and the silence below is a refusal"
        )
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(
            inputFrames(connection), armed,
            "a touch that only puts the keyboard away must not report a focus-out for a terminal that never had focus"
        )
    }

    // MARK: - TerminalSessionModel.dismissSoftKeyboard

    func testDismissSoftKeyboardDropsTheTerminalResponderAndReleasesComposerFocus() async throws {
        let model = TerminalTestSupport.model()
        hostInKeyWindow(model.terminalView)
        model.showTerminalKeyboard()
        XCTAssertTrue(model.terminalKeyboardActive)
        let focusEvents = StreamCollector(model.focusRequests())

        model.dismissSoftKeyboard()

        XCTAssertFalse(model.terminalKeyboardActive)
        XCTAssertFalse(model.terminalView.isFirstResponder)
        XCTAssertFalse(model.terminalView.keyboardGate, "a closed gate is what stops the next touch re-raising it")
        let focused = try await focusEvents.next()
        XCTAssertFalse(
            focused, "false releases the composer field — true would re-raise the very keyboard this puts away"
        )
    }

    /// The composer path, which is the one with no escape today: the terminal
    /// never became first responder, so an emission guarded on that state would
    /// leave the keyboard up.
    func testDismissSoftKeyboardReleasesAComposerRaisedKeyboardAndKeepsTheField() async throws {
        let model = TerminalTestSupport.model()
        hostInKeyWindow(model.terminalView)
        model.setInput("half-typed prompt", requestFocus: false)
        XCTAssertFalse(model.terminalKeyboardActive, "focusing the composer never raises the terminal responder")
        let focusEvents = StreamCollector(model.focusRequests())

        model.dismissSoftKeyboard()

        let focused = try await focusEvents.next()
        XCTAssertFalse(focused)
        XCTAssertTrue(model.composerVisible, "dismissing is not hiding: collapsing the composer is what loses the field")
        XCTAssertEqual(model.inputText, "half-typed prompt")
    }

    // MARK: - What counts as covering

    /// The two lifts `TerminalKeyboardObserverTests` pins from real
    /// iPhone-portrait geometry, recomputed here through the shipped formula so
    /// the threshold is checked against a device measurement rather than a
    /// number copied into the test.
    private static let windowBounds = CGRect(x: 0, y: 0, width: 390, height: 844)
    private static let bottomSafeInset: CGFloat = 34

    private func lift(ofKeyboardFrame frame: CGRect) -> CGFloat {
        TerminalKeyboardPan.lift(
            keyboardEndFrame: frame,
            windowBounds: Self.windowBounds,
            bottomSafeInset: Self.bottomSafeInset
        )
    }

    /// What iOS leaves in the keyboard's place once a hardware keyboard is
    /// attached: a 69pt bar on the window bottom, spanning its full width.
    private var assistantBarLift: CGFloat {
        lift(ofKeyboardFrame: CGRect(x: 0, y: 775, width: 390, height: 69))
    }

    private var dockedKeyboardLift: CGFloat {
        lift(ofKeyboardFrame: CGRect(x: 0, y: 500, width: 390, height: 344))
    }

    func testTheAssistantBarAHardwareKeyboardLeavesIsNotCovering() throws {
        let (_, terminal, scroll) = hostedTerminal()
        var dismissals = 0
        terminal.onDismissSoftKeyboard = { dismissals += 1 }
        XCTAssertGreaterThan(
            assistantBarLift, 0,
            "the bar is bottom-anchored and full width, so `lift` reports it — which is exactly why a "
                + "`> 0` predicate treated a hardware keyboard as covering"
        )
        scroll.setKeyboardLift(assistantBarLift, duration: 0)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertFalse(terminal.isSoftKeyboardCovering, "a 35pt bar hides no output, so there is nothing to put away")
        XCTAssertEqual(dismissals, 0)
        XCTAssertFalse(terminal.dismissedKeyboardOnCurrentTouch)
        let tap = try rowTap(on: terminal)
        XCTAssertTrue(
            terminal.gestureRecognizerShouldBegin(tap),
            "with a hardware keyboard attached every tap is an ordinary tap — swallowing them all is what "
                + "the wrong predicate did"
        )
    }

    func testARealDockedKeyboardStillClearsTheThreshold() {
        let (_, terminal, scroll) = hostedTerminal()
        var dismissals = 0
        terminal.onDismissSoftKeyboard = { dismissals += 1 }
        scroll.setKeyboardLift(dockedKeyboardLift, duration: 0)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertTrue(
            terminal.isSoftKeyboardCovering,
            "310pt is a third of the screen: a threshold raised past a real keyboard would strand it up "
                + "with no way down, which is the whole bug this feature exists for"
        )
        XCTAssertEqual(dismissals, 1)
    }

    /// The cost of getting the predicate wrong: the terminal responder is what a
    /// hardware keyboard types into, so resigning it on every touch ends typing.
    func testAHardwareKeyboardKeepsTypingThroughATerminalTouch() {
        let (model, terminal, scroll) = hostedTerminal()
        hostInKeyWindow(scroll)
        model.showTerminalKeyboard()
        XCTAssertTrue(model.terminalKeyboardActive, "the harness must be typing into the terminal before the touch lands")
        scroll.setKeyboardLift(assistantBarLift, duration: 0)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertTrue(model.terminalKeyboardActive)
        XCTAssertTrue(
            terminal.isFirstResponder,
            "the Coordinator wire is live here, so a covering verdict would have resigned the responder "
                + "and dropped every following keystroke"
        )
    }

    // MARK: - Taking the touch away from the tap recognizers

    /// Records every write to `isEnabled`. Without it a
    /// `spendSingleTapRecognizers()` that did nothing at all — or that swept a
    /// recognizer it had to leave alone — would satisfy the end-state
    /// assertions, since spending restores what it found.
    private final class RecordingTapGesture: UITapGestureRecognizer {
        private(set) var enabledWrites: [Bool] = []

        override var isEnabled: Bool {
            get { super.isEnabled }
            set {
                enabledWrites.append(newValue)
                super.isEnabled = newValue
            }
        }
    }

    private func taps(on terminal: MobaideTerminalView, mobaideOwned: Bool) -> [UIGestureRecognizer] {
        (terminal.gestureRecognizers ?? []).filter {
            $0 is UITapGestureRecognizer && ($0.name?.hasPrefix("mobaide.") == true) == mobaideOwned
        }
    }

    /// `mobaide.rowTap` is still pending ~300ms after this touch lifts — long
    /// enough for a following touch-down to recompute the latch to false. Refusing
    /// it at `gestureRecognizerShouldBegin` alone does not reach that far, so the
    /// touch has to be taken away from the recognizers outright.
    func testOnlyTheDismissingTouchSpendsAndRearmsTheMobaideTaps() {
        let (_, terminal, scroll) = hostedTerminal()
        // Stands in for `mobaide.rowTap`: same class, same single-tap count, so
        // the loop treats it identically — but it can say whether it was
        // touched at all.
        let probe = RecordingTapGesture()
        probe.name = "mobaide.rowTapProbe"
        terminal.addGestureRecognizer(probe)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertEqual(probe.enabledWrites, [], "a touch that dismissed nothing must leave the pending taps to resolve")

        scroll.setKeyboardLift(dockedKeyboardLift, duration: 0)
        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertEqual(
            probe.enabledWrites, [false, true],
            "disabling is what cancels the in-flight tracking; re-enabling arms it for the next touch, "
                + "not for this one"
        )
        let mobaideTaps = taps(on: terminal, mobaideOwned: true)
        XCTAssertGreaterThanOrEqual(mobaideTaps.count, 4, "rowTap, rowDoubleTap, mouseTap and the probe")
        for tap in mobaideTaps {
            XCTAssertTrue(
                tap.isEnabled,
                "\(tap.name ?? "unnamed") was left disabled — the next touch would find a dead surface, not one extra tap"
            )
        }
    }

    private func tapsRequired(_ name: String, on terminal: MobaideTerminalView) throws -> Int {
        let tap = try XCTUnwrap(
            (terminal.gestureRecognizers ?? []).first { $0.name == name } as? UITapGestureRecognizer
        )
        return tap.numberOfTapsRequired
    }

    /// Spending `mobaide.rowDoubleTap` on the dismissing touch delivers the user's
    /// *second* tap as a fresh first one: the double tap never recognizes, and a
    /// gesture whose entire purpose is copying a command into the composer to read
    /// it before running instead runs it.
    func testTheDismissingTouchLeavesMultiTapRecognizersArmed() throws {
        let (_, terminal, scroll) = hostedTerminal()
        let singleTapProbe = RecordingTapGesture()
        singleTapProbe.name = "mobaide.rowTapProbe"
        let doubleTapProbe = RecordingTapGesture()
        doubleTapProbe.name = "mobaide.rowDoubleTapProbe"
        doubleTapProbe.numberOfTapsRequired = 2
        terminal.addGestureRecognizer(singleTapProbe)
        terminal.addGestureRecognizer(doubleTapProbe)
        // The probes only stand in for the shipped recognizers while the tap
        // count that decides membership is the one they carry.
        let rowDoubleTapTaps = try tapsRequired("mobaide.rowDoubleTap", on: terminal)
        let rowTapTaps = try tapsRequired("mobaide.rowTap", on: terminal)
        let mouseTapTaps = try tapsRequired("mobaide.mouseTap", on: terminal)
        XCTAssertEqual(rowDoubleTapTaps, 2)
        XCTAssertEqual(rowTapTaps, 1)
        XCTAssertEqual(mouseTapTaps, 1)
        scroll.setKeyboardLift(dockedKeyboardLift, duration: 0)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertEqual(
            doubleTapProbe.enabledWrites, [],
            "cancelling the double tap is what turns the second half of a copy gesture into an execute"
        )
        XCTAssertEqual(
            singleTapProbe.enabledWrites, [false, true],
            "a single tap can still act on this touch alone, so it is the one that has to be taken away"
        )
    }

    func testTheDismissingTouchLeavesSwiftTermsParkedTapsDisabled() {
        let (_, terminal, scroll) = hostedTerminal()
        // What SwiftTerm installs and `neutralizeSwiftTermGestures()` parks: an
        // unnamed tap whose action calls `becomeFirstResponder()`.
        terminal.addGestureRecognizer(UITapGestureRecognizer())
        terminal.neutralizeSwiftTermGestures()
        let parked = taps(on: terminal, mobaideOwned: false)
        XCTAssertFalse(parked.isEmpty, "there has to be a parked tap, or the `isEnabled` filter has nothing to spare")
        XCTAssertTrue(parked.allSatisfy { !$0.isEnabled })
        scroll.setKeyboardLift(dockedKeyboardLift, duration: 0)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        for tap in taps(on: terminal, mobaideOwned: false) {
            XCTAssertFalse(
                tap.isEnabled,
                "re-arming a parked tap hands a terminal touch back the becomeFirstResponder the keyboard gate forbids"
            )
        }
    }

    // MARK: - The Coordinator's agreement latches

    /// A recognizer that never saw a finger reports `.zero` for every location,
    /// so only the point is stubbed — the view, the handler and the emulator
    /// behind them all stay real.
    private final class StubTapGesture: UITapGestureRecognizer {
        var point: CGPoint = .zero
        override func location(in view: UIView?) -> CGPoint { point }
    }

    /// Invokes a Coordinator tap handler through the very selector
    /// `attachGestures` resolves, so what runs is the shipped gate.
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

    private func promptRowPoint(_ terminal: MobaideTerminalView) -> CGPoint {
        terminal.feed(text: "> echo hi")
        return CGPoint(x: terminal.cellSize.width * 5.5, y: terminal.cellSize.height * 0.5)
    }

    /// Every frame a tap can produce: the CR rides `i`, an historical prompt `x`.
    private func tapFrames(_ connection: FakeWebSocketConnection) -> [Data] {
        connection.sentBinaries.filter { $0.first == 0x69 || $0.first == 0x78 }
    }

    private func waitForTapFrames(_ connection: FakeWebSocketConnection, atLeast count: Int) async -> [Data] {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline, tapFrames(connection).count < count {
            try? await Task.sleep(for: .milliseconds(2))
        }
        return tapFrames(connection)
    }

    /// Bytes the emulator itself writes ride the same `i` frame as typed input.
    private func inputFrames(_ connection: FakeWebSocketConnection) -> [Data] {
        connection.sentBinaries.filter { $0.first == 0x69 }
    }

    private func inputFrame(_ text: String) -> Data {
        Data([0x69]) + Data(text.utf8)
    }

    private func waitForInputFrames(_ connection: FakeWebSocketConnection, atLeast count: Int) async -> [Data] {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline, inputFrames(connection).count < count {
            try? await Task.sleep(for: .milliseconds(2))
        }
        return inputFrames(connection)
    }

    private func liveConnection(
        _ model: TerminalSessionModel, _ factory: FakeWebSocketTransportFactory
    ) async throws -> FakeWebSocketConnection {
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline, !(model.connectionState == .live && model.isRunning) {
            try await Task.sleep(for: .milliseconds(2))
        }
        XCTAssertTrue(
            model.connectionState == .live && model.isRunning,
            "sendTappedRow drops everything off a live connection, so an unwired harness would pass on its own"
        )
        return connection
    }

    /// `handleMouseTap` takes the lift-time reading the two latches normally hold —
    /// so the dismissal callback is the only thing that writes them here. Without
    /// it they would still carry the previous tap's answer, letting a delayed row
    /// tap resolve a row a program painted behind a freeze.
    func testARowTapAfterADismissingTouchSendsNothing() async throws {
        let factory = FakeWebSocketTransportFactory()
        let (model, terminal, scroll) = hostedTerminal(transportFactory: factory)
        let coordinator = try XCTUnwrap(retainedCoordinator)
        let connection = try await liveConnection(model, factory)
        let point = promptRowPoint(terminal)
        XCTAssertEqual(
            terminal.resolveRow(at: point)?.text, "echo hi",
            "the row must resolve, or nothing but the latches could be keeping the CR off the wire"
        )
        XCTAssertEqual(terminal.wheelScrollMode, .localScrollback, "and the app must own the pointer, the arm that normally submits")
        scroll.setKeyboardLift(dockedKeyboardLift, duration: 0)

        terminal.touchesBegan(Set<UITouch>(), with: nil)
        fire("handleSingleTap:", on: coordinator, view: terminal, at: point)

        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            tapFrames(connection).isEmpty,
            "the dismissing touch has to close both latches itself — a stale `false` here resends a row "
                + "nobody tapped"
        )
        model.sendTappedRow(text: "echo hi", isCurrentInput: true)
        let carried = await waitForTapFrames(connection, atLeast: 1)
        XCTAssertEqual(
            carried.count, 1,
            "and the wire does carry a tap's frame, so the refusal above is the gate and not a dead harness"
        )
    }

    func testADoubleTapAfterADismissingTouchCopiesNothing() throws {
        let (model, terminal, scroll) = hostedTerminal()
        let coordinator = try XCTUnwrap(retainedCoordinator)
        let point = promptRowPoint(terminal)
        XCTAssertEqual(terminal.resolveRow(at: point)?.text, "echo hi", "only the frozen-display latch can be refusing")
        scroll.setKeyboardLift(dockedKeyboardLift, duration: 0)

        terminal.touchesBegan(Set<UITouch>(), with: nil)
        fire("handleDoubleTap:", on: coordinator, view: terminal, at: point)

        XCTAssertEqual(
            model.inputText, "",
            "spoofed text in the composer is the first half of the same attack, so it fails closed too"
        )
    }

    /// Stands in for a recognizer that resolves *inside* the sweep: disabling a
    /// recognizer releases whatever required it to fail, and the freed
    /// recognizer's action can run before `touchesBegan` returns.
    private final class ReentrantTapGesture: UITapGestureRecognizer {
        var onDisable: (@MainActor () -> Void)?

        override var isEnabled: Bool {
            get { super.isEnabled }
            set {
                super.isEnabled = newValue
                if !newValue { onDisable?() }
            }
        }
    }

    /// Spending a recognizer can hand a tap its answer immediately, and the answer
    /// it reads is written by the dismissal callback — so the callback has to have
    /// run first, leaving no window in which a tap freed mid-sweep resolves a row
    /// against the previous touch's readings.
    func testTheKeyboardIsPutAwayBeforeAnyRecognizerIsSpent() throws {
        let (model, terminal, scroll) = hostedTerminal()
        let coordinator = try XCTUnwrap(retainedCoordinator)
        let point = promptRowPoint(terminal)
        XCTAssertEqual(
            terminal.resolveRow(at: point)?.text, "echo hi",
            "the row has to resolve, or the latches are not what is refusing"
        )
        var reentries = 0
        let probe = ReentrantTapGesture()
        probe.name = "mobaide.rowTapProbe"
        probe.onDisable = { [weak self, weak coordinator] in
            guard let self, let coordinator else { return }
            reentries += 1
            fire("handleDoubleTap:", on: coordinator, view: terminal, at: point)
        }
        terminal.addGestureRecognizer(probe)
        scroll.setKeyboardLift(dockedKeyboardLift, duration: 0)

        terminal.touchesBegan(Set<UITouch>(), with: nil)

        XCTAssertEqual(reentries, 1, "the sweep has to reach the probe, or nothing was asked to resolve mid-touch")
        XCTAssertEqual(
            model.inputText, "",
            "the fail-closed latches must already be armed when the first recognizer is touched — spending "
                + "first leaves a tap freed by the sweep reading a stale `false`"
        )
    }

    /// More output than one screen holds, so `contentSize` really exceeds the
    /// viewport and there is scrollback for a drag to move.
    private func feedTranscript(_ terminal: MobaideTerminalView) {
        var text = ""
        for index in 0..<200 {
            text += "line \(index)\r\n"
        }
        terminal.feed(text: text)
    }
}
