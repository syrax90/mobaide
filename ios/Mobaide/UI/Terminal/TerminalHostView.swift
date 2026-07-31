@preconcurrency import SwiftTerm
import SwiftUI
import UIKit

/// `TerminalView` subclass with an explicit-only on-screen keyboard (Android
/// parity): the keyboard raises solely from the InputBar's show-keyboard
/// button — never from touching the terminal — and typed keys flow through
/// SwiftTerm's existing `send` delegate path straight to the PTY. SwiftTerm's
/// built-in input accessory (the persistent sticky-Ctrl toggle) stays removed;
/// the app's per-kind control-key row is the only accessory surface.
final class MobaideTerminalView: TerminalView {
    /// Explicit-only keyboard gate: opened by the model just before it calls
    /// `becomeFirstResponder()`, closed on every resign or failed raise. While
    /// closed, nothing — including any UIKit responder plumbing — can raise
    /// the keyboard from a terminal touch (the Android regression this model
    /// replaced: "keyboard appears every time you tap anywhere").
    var keyboardGate = false

    /// Reports actual first-responder transitions to the owning model so the
    /// show/hide toggle reflects reality, not the last request.
    var onFirstResponderChange: ((Bool) -> Void)?

    override var canBecomeFirstResponder: Bool { keyboardGate }

    override func becomeFirstResponder() -> Bool {
        let became = super.becomeFirstResponder()
        if became {
            onFirstResponderChange?(true)
        } else {
            keyboardGate = false
        }
        return became
    }

    override func resignFirstResponder() -> Bool {
        let resigned = super.resignFirstResponder()
        if resigned {
            keyboardGate = false
            onFirstResponderChange?(false)
        }
        return resigned
    }

    func configureAppearance() {
        nativeBackgroundColor = UIColor(MobaideColors.terminalBackground)
        nativeForegroundColor = UIColor(MobaideColors.terminalForeground)
        backgroundColor = UIColor(MobaideColors.terminalBackground)
        font = UIFont.monospacedSystemFont(ofSize: 13, weight: .regular)
        // Drop SwiftTerm's `TerminalAccessory` (sticky-Ctrl bar) installed by
        // its `setup()`. The property is settable-public (not overridable), and
        // the accessory is installed exactly once at init, so a one-time nil
        // here — before the view can ever become first responder — is final.
        inputAccessoryView = nil
        installVerticalScrollGesture()
        userScrollObserver.terminal = self
        delegate = userScrollObserver
    }

    /// SwiftTerm's per-cell dimensions. The caret is exactly one cell, so its
    /// frame exposes SwiftTerm's internal `cellDimension` precisely — letting a
    /// fixed column count map to an exact pixel width with no font-metric
    /// guessing. Falls back to SwiftTerm's own pixel-snapped `"W"` measurement
    /// only in the rare window before the caret view exists.
    var cellSize: CGSize {
        let caret = caretFrame.size
        if caret.width >= 1, caret.height >= 1 { return caret }
        let measured = ("W" as NSString).size(withAttributes: [.font: font])
        let scale = UIScreen.main.scale
        let snap = { (value: CGFloat) in max(1, ceil(value * scale) / scale) }
        return CGSize(width: snap(measured.width), height: snap(measured.height))
    }

    /// Resolves a tap point (in this view's content coordinates) to a prompt
    /// row's command text and whether that row holds the current input. Returns
    /// nil for rows without a recognizable prompt prefix so taps on plain
    /// output never execute anything.
    func resolveRow(at point: CGPoint) -> (text: String, isCurrentInput: Bool)? {
        let cellHeight = cellSize.height
        guard cellHeight > 0, point.y >= 0 else { return nil }
        // A single floor over the absolute content position. Flooring the
        // viewport row and the window origin separately drops a row whenever
        // the offset is not cell-aligned — which a drag, and the sub-cell
        // remainder `scrollToBottom()` lands on, both make the normal case.
        let contentRow = Int(point.y / cellHeight)
        let terminal = getTerminal()
        // `yDisp` is pinned to `yBase`, so it is the first row of the active
        // region as well as the largest legal scrollback window origin.
        let activeRegionTop = terminal.buffer.yDisp
        guard let line = lineOnScreen(contentRow: contentRow, cellHeight: cellHeight) else { return nil }
        guard let command = Self.promptCommand(line.translateToString(trimRight: true)) else { return nil }
        let (_, cursorY) = terminal.getCursorLocation()
        // `getCursorLocation()` is relative to the active region, so the tapped
        // row is the live input only when it is that region's cursor row — and
        // only while following: scrolled into history every prompt is historical.
        let isCurrentInput = isScrolledToBottom && contentRow - activeRegionTop == cursorY
        return (command, isCurrentInput)
    }

    /// The buffer line rendered at absolute content row `contentRow`, or nil
    /// when nothing is drawn there.
    ///
    /// `getLine(row:)` counts from `buffer.yDisp`, but the iOS renderer counts
    /// from `contentOffset`, and SwiftTerm never moves `yDisp` for a user drag
    /// (`Terminal.userScrolling` is never set, so `yDisp` stays pinned to
    /// `yBase`) — once scrolled up the two name different lines. `yDisp` is
    /// public and `getLine` is a synchronous pure read, so the window is
    /// re-pointed at the rows actually on screen for the lookup and restored
    /// before any SwiftTerm code can observe it.
    ///
    /// The equality holds against `terminal.buffer`. SwiftTerm draws
    /// `displayBuffer`, which diverges into a frozen deep copy while a program
    /// holds synchronized output (DEC mode 2026); that buffer is `internal`, so
    /// a tap taken during such a freeze reads the live line rather than the
    /// drawn one.
    private func lineOnScreen(contentRow: Int, cellHeight: CGFloat) -> BufferLine? {
        let terminal = getTerminal()
        let buffer = terminal.buffer
        let restore = buffer.yDisp
        let visibleTop = min(max(0, Int(contentOffset.y / cellHeight)), restore)
        // `getLine` addresses exactly `rows` lines from the window origin, so an
        // origin a fraction of a cell above the tail — where `scrollToBottom()`
        // parks — would push the bottom-most line out of reach. Raise the window
        // just far enough to cover the tapped row, but never past `yDisp`:
        // beyond it `row + yDisp` runs off the end of `lines`, and
        // `CircularList` wraps instead of trapping, so an `ESC[3J` scrollback
        // wipe (which leaves `contentSize` stale) could otherwise resolve a
        // recycled line.
        let topRow = min(max(visibleTop, contentRow - terminal.getDims().rows + 1), restore)
        let row = contentRow - topRow
        guard row >= 0 else { return nil }
        guard topRow != restore else { return terminal.getLine(row: row) }
        buffer.yDisp = topRow
        defer { buffer.yDisp = restore }
        return terminal.getLine(row: row)
    }

    /// Android `getPromptPrefixLength`: `❯` + NBSP, `❯` + space, or ASCII `> `.
    static func promptCommand(_ row: String) -> String? {
        for prefix in ["\u{276F}\u{00A0}", "\u{276F} ", "> "] where row.hasPrefix(prefix) {
            let command = String(row.dropFirst(prefix.count)).trimmingCharacters(in: .whitespaces)
            return command.isEmpty ? nil : command
        }
        return nil
    }

    /// Disables SwiftTerm's own gesture recognizers that would steal touches
    /// from the app's surfaces, keeping the `mobaide.*` recognizers and the
    /// scroll views' own pans intact:
    /// - SwiftTerm's single-tap calls `becomeFirstResponder()` (raising the
    ///   keyboard from a terminal touch — the exact behavior the explicit-only
    ///   keyboard model forbids) and, while first responder, sends mouse click
    ///   reports / pops context menus that double-fire alongside the app's
    ///   `mobaide.rowTap` semantics — so every SwiftTerm tap is disabled;
    /// - double/triple-tap and long-press start text selection, hijacking the
    ///   Static-mode horizontal pan into a selection drag;
    /// - the plain `UIPanGestureRecognizer` SwiftTerm adds while a TUI has
    ///   mouse reporting enabled (Claude Code uses any-event tracking) sends
    ///   button-drag mouse events the TUIs ignore, and being on the deeper
    ///   view it swallows every drag — killing both the horizontal pan and
    ///   vertical scrollback. The exact-class check keeps
    ///   `UIScrollViewPanGestureRecognizer` (the scrollback driver) alive.
    func neutralizeSwiftTermGestures() {
        for recognizer in gestureRecognizers ?? [] where recognizer.name?.hasPrefix("mobaide.") != true {
            if recognizer is UILongPressGestureRecognizer {
                recognizer.isEnabled = false
            } else if recognizer is UITapGestureRecognizer {
                recognizer.isEnabled = false
            } else if type(of: recognizer) == UIPanGestureRecognizer.self, recognizer !== panGestureRecognizer {
                recognizer.isEnabled = false
            }
        }
    }

    /// SwiftTerm adds its mouse-reporting pan lazily when the TUI turns mouse
    /// mode on (which can happen at any time, e.g. during replay), so the
    /// neutralization sweep must re-run on every mouse-mode transition.
    override func mouseModeChanged(source: Terminal) {
        super.mouseModeChanged(source: source)
        neutralizeSwiftTermGestures()
    }

    // MARK: - Vertical scrolling

    static let verticalScrollGestureName = "mobaide.verticalScroll"

    private(set) var verticalScrollGesture: UIPanGestureRecognizer?
    private let verticalScrollDelegate = ExclusiveVerticalPanDelegate()
    private var wheelAccumulator = TerminalWheelAccumulator()
    /// The cell the current drag started on. Every wheel event of a gesture
    /// reports it, so a drag that leaves the pane it began in keeps scrolling
    /// that pane (Android pins identically).
    private var wheelAnchor: CGPoint = .zero

    /// Where this drag's rows go, re-read from the emulator on every touch so
    /// nothing has to be re-asserted on replay, reconnect or a screen revisit.
    var wheelScrollMode: TerminalWheelScroll.Mode {
        let terminal = getTerminal()
        return TerminalWheelScroll.mode(
            isMouseReporting: terminal.mouseMode != .off,
            isAlternateBuffer: terminal.isCurrentBufferAlternate
        )
    }

    /// Idempotent: the recognizer targets this view rather than the per-visit
    /// Coordinator, so it survives screen revisits untouched.
    func installVerticalScrollGesture() {
        if let existing = verticalScrollGesture, existing.view === self { return }
        let pan = UIPanGestureRecognizer(target: self, action: #selector(handleVerticalScroll(_:)))
        pan.name = Self.verticalScrollGestureName
        pan.delegate = verticalScrollDelegate
        pan.maximumNumberOfTouches = 1
        addGestureRecognizer(pan)
        verticalScrollGesture = pan
    }

    /// Whether SwiftTerm's own scrollback pan may own a touch.
    ///
    /// Mouse-reporting and alternate-screen programs own the vertical axis
    /// (Android `doScroll` ladder), so the scrollback pan must not also run —
    /// in the alternate buffer it has nothing to scroll anyway.
    ///
    /// The nested pans recognize simultaneously, so in Static mode an
    /// outer-claimed horizontal drag would otherwise also drag the scrollback.
    /// Refusing exactly the drags the outer claims is the child half of that
    /// split (Android `requestDisallowInterceptTouchEvent` on a vertically
    /// dominant `onScroll`). It replaces `isDirectionalLockEnabled` on this
    /// view, which latched onto whichever axis started dominant — including the
    /// horizontal one, where this view has no overflow to scroll — and then
    /// swallowed the rest of the touch.
    static func allowsScrollbackPan(
        mode: TerminalWheelScroll.Mode,
        outerClaimsHorizontalPan: Bool
    ) -> Bool {
        mode == .localScrollback && !outerClaimsHorizontalPan
    }

    /// The same decision resolved against live state: the emulator's current
    /// ladder arm, and the host found through the view hierarchy rather than a
    /// cached back-reference — SwiftUI can keep an outgoing wrapper alive after
    /// a newer one has re-parented this shared view. A drag with no host is
    /// uncontested and stays the scrollback's.
    func allowsScrollbackPan(velocity: CGPoint, translation: CGPoint) -> Bool {
        let host = superview as? TerminalPanScrollView
        return Self.allowsScrollbackPan(
            mode: wheelScrollMode,
            outerClaimsHorizontalPan: host?.wouldClaimHorizontalPan(
                velocity: velocity,
                translation: translation
            ) ?? false
        )
    }

    /// Vertical-axis arbitration, decided per touch rather than stored. UIKit
    /// asks the view this for every recognizer attached to it, so it can refuse
    /// SwiftTerm's own scrollback pan without touching `isScrollEnabled` — which
    /// would have to be re-asserted on buffer switches that `mouseModeChanged`
    /// never reports.
    override func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        if gestureRecognizer === panGestureRecognizer {
            // Sampled from *this* recognizer — the one UIKit is asking to begin,
            // and so the only one certain to be live at this moment.
            guard allowsScrollbackPan(
                velocity: panGestureRecognizer.velocity(in: self),
                translation: panGestureRecognizer.translation(in: self)
            ) else { return false }
            return super.gestureRecognizerShouldBegin(gestureRecognizer)
        }
        if gestureRecognizer === verticalScrollGesture {
            guard wheelScrollMode != .localScrollback,
                  let pan = gestureRecognizer as? UIPanGestureRecognizer else { return false }
            return TerminalWheelScroll.claimsPan(
                velocity: pan.velocity(in: self),
                translation: pan.translation(in: self)
            )
        }
        return super.gestureRecognizerShouldBegin(gestureRecognizer)
    }

    @objc private func handleVerticalScroll(_ gesture: UIPanGestureRecognizer) {
        switch gesture.state {
        case .began:
            wheelAccumulator.reset()
            wheelAnchor = gesture.location(in: self)
            // Consume the slop travel too, matching Android's GestureDetector
            // whose first `onScroll` already includes it.
            consumeVerticalScroll(gesture)
        case .changed:
            consumeVerticalScroll(gesture)
        case .ended, .cancelled, .failed:
            wheelAccumulator.reset()
        default:
            break
        }
    }

    private func consumeVerticalScroll(_ gesture: UIPanGestureRecognizer) {
        let deltaY = gesture.translation(in: self).y
        gesture.setTranslation(.zero, in: self)
        let rows = wheelAccumulator.rowsDown(panDeltaY: deltaY, cellHeight: cellSize.height)
        guard rows != 0 else { return }
        emitVerticalScroll(rowsDown: rows, at: wheelAnchor)
    }

    /// Emits one scroll step per row. The ladder arm is re-derived per call, so
    /// output that flips the buffer or mouse mode mid-drag is honored.
    func emitVerticalScroll(rowsDown: Int, at point: CGPoint) {
        let mode = wheelScrollMode
        guard mode != .localScrollback, rowsDown != 0 else { return }
        let terminal = getTerminal()
        let dims = terminal.getDims()
        // A stalled main thread can deliver one huge delta; capping at a page
        // keeps a single callback from flushing hundreds of events down the
        // WebSocket. Android has no cap because its PTY is local.
        let steps = min(abs(rowsDown), max(1, dims.rows))
        let down = rowsDown > 0
        let cell = TerminalWheelScroll.cell(
            at: point,
            contentOffset: contentOffset,
            cellSize: cellSize,
            cols: dims.cols,
            rows: dims.rows
        )
        switch mode {
        case .wheel:
            let flags = TerminalWheelScroll.wheelFlags(down: down)
            for _ in 0 ..< steps {
                terminal.sendEvent(buttonFlags: flags, x: cell.col, y: cell.row)
            }
        case .cursorKeys:
            // Concatenated into one write: a PTY is a byte stream, so N arrows in
            // one write are indistinguishable from N writes to the program, while
            // one write costs one WebSocket frame instead of `steps` of them.
            // `send(data:)` also runs SwiftTerm's snap-to-bottom, which is a
            // no-op here: this arm only runs on the alternate screen, whose
            // buffer has no scrollback to be snapped away from.
            let bytes = TerminalWheelScroll.cursorKeyBytes(
                down: down,
                applicationCursor: terminal.applicationCursor
            )
            var batch: [UInt8] = []
            batch.reserveCapacity(bytes.count * steps)
            for _ in 0 ..< steps {
                batch.append(contentsOf: bytes)
            }
            send(data: batch[...])
        case .localScrollback:
            break
        }
    }

    // MARK: - Follow mode

    private let userScrollObserver = UserScrollObserver()

    /// Reports the scroll position of a user-driven drag or fling. Programmatic
    /// offset writes never reach it — see `handleScrollViewDidScroll`.
    var onUserScroll: ((Double) -> Void)?

    /// While true, SwiftTerm's snap-to-tail writes are refused so a paused
    /// viewport stays where the user left it (Android
    /// `TerminalEmulator.isAutoScrollDisabled`). Owned by the session model,
    /// which keeps it in lockstep with follow mode.
    var isAutoScrollSuppressed = false

    /// True while PTY bytes are being handed to the emulator. SwiftTerm's
    /// snap-to-tail runs synchronously inside that call (`Terminal.scroll` →
    /// `scrolled(source:yDisp:)` → `updateScroller()`), and this is the only way
    /// to tell it apart from a genuine drag: UIKit reports both as a scroll of
    /// the same scroll view, so a line that lands mid-gesture would otherwise
    /// read as the user returning to the tail and silently resume follow mode.
    private(set) var isApplyingEmulatorOutput = false

    /// Feed PTY bytes to the emulator, attributing any offset move they cause.
    func applyingEmulatorOutput(_ body: () -> Void) {
        isApplyingEmulatorOutput = true
        defer { isApplyingEmulatorOutput = false }
        body()
    }

    var maxScrollOffsetY: CGFloat {
        TerminalFollowScroll.maxOffsetY(contentHeight: contentSize.height, viewportHeight: bounds.height)
    }

    var followPosition: Double {
        TerminalFollowScroll.position(offsetY: contentOffset.y, maxOffsetY: maxScrollOffsetY)
    }

    var isScrolledToBottom: Bool {
        TerminalFollowScroll.isAtBottom(position: followPosition)
    }

    /// SwiftTerm's `updateScroller()` and `ensureCaretIsVisible()` yank the
    /// offset to the tail on every scrolled line and every byte sent, and both
    /// are `internal`. `contentOffset` is `open`, so the snap is refused here
    /// instead — before it applies, so a held viewport never flickers.
    override var contentOffset: CGPoint {
        get { super.contentOffset }
        set {
            // A snap arriving mid-gesture has to be refused too, so the
            // emulator attribution stands alongside the quiescent case rather
            // than replacing it. The gesture clause still lets UIKit drive the
            // offset freely: a drag back to the tail is a write to the tail
            // from above, which is exactly what `holdsOffset` refuses.
            guard isAutoScrollSuppressed,
                  isApplyingEmulatorOutput || (!isDragging && !isDecelerating && !isTracking) else {
                super.contentOffset = newValue
                return
            }
            let current = super.contentOffset
            let holds = TerminalFollowScroll.holdsOffset(
                incomingY: newValue.y,
                currentY: current.y,
                maxOffsetY: maxScrollOffsetY
            )
            super.contentOffset = holds ? CGPoint(x: newValue.x, y: current.y) : newValue
        }
    }

    /// Replaces SwiftTerm's `scroll(toPosition: 1.0)`, which cannot work on iOS:
    /// it routes through `scrollTo(row:)`, whose body is guarded by
    /// `row != yDisp` — and `yDisp` is already pinned to the tail.
    func scrollToBottom() {
        let maxY = maxScrollOffsetY
        // Tolerance, not equality: SwiftTerm parks the tail at
        // `(lines.count - rows) * cellHeight`, which overshoots `maxScrollOffsetY`
        // by the viewport's sub-cell remainder, so an exact comparison never
        // matches and every sent byte would jitter the viewport by up to a row
        // and force a full-terminal repaint.
        guard contentOffset.y < maxY - TerminalFollowScroll.bottomTolerance else { return }
        contentOffset = CGPoint(x: contentOffset.x, y: maxY)
    }

    /// - Parameter isUserDriven: whether the offset moved because of a touch.
    ///   The gate is the feedback-loop guard, and the direct analogue of the
    ///   Android fork rule that `setTopRow` must not report a position
    ///   (`docs/terminal-view-fork-changes.md`): UIKit delivers
    ///   `scrollViewDidScroll` for plain `contentOffset` assignments too, so
    ///   without it SwiftTerm's snap-to-tail — and `scrollToBottom()` itself —
    ///   would resume follow mode behind the user's back.
    func handleScrollViewDidScroll(isUserDriven: Bool) {
        guard isUserDriven, !isApplyingEmulatorOutput else { return }
        onUserScroll?(followPosition)
    }
}

/// Turns the terminal's own scroll view into the follow-mode signal SwiftTerm
/// never provides: it declares `UIScrollViewDelegate` conformance but implements
/// no method of it and never claims the `delegate` slot, so a drag moves
/// `contentOffset` — which is what the iOS renderer draws from — without ever
/// reaching `TerminalViewDelegate.scrolled`.
///
/// A separate object rather than the view itself, matching
/// `ExclusiveVerticalPanDelegate`: `MobaideTerminalView` inherits SwiftTerm's
/// empty conformance, so answering the protocol on the view would silently make
/// every unimplemented callback the scroll view's own.
@MainActor
private final class UserScrollObserver: NSObject, UIScrollViewDelegate {
    weak var terminal: MobaideTerminalView?

    func scrollViewDidScroll(_ scrollView: UIScrollView) {
        terminal?.handleScrollViewDidScroll(
            isUserDriven: scrollView.isDragging || scrollView.isDecelerating
        )
    }
}

/// Denies simultaneous recognition for the vertical-scroll pan so exactly one of
/// {outer horizontal pan, SwiftTerm scrollback pan, this} drives a touch.
///
/// It is a separate object on purpose: `MobaideTerminalView` is a `UIScrollView`,
/// which is the delegate of its own pan through a private implementation, and
/// `shouldRecognizeSimultaneouslyWith` is an optional protocol method with no
/// callable `super` — answering it on the view would replace UIKit's nested-pan
/// rule and break the Static-mode horizontal arbitration.
@MainActor
private final class ExclusiveVerticalPanDelegate: NSObject, UIGestureRecognizerDelegate {
    func gestureRecognizer(
        _ gestureRecognizer: UIGestureRecognizer,
        shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer
    ) -> Bool {
        false
    }
}

/// Bridges the retained `MobaideTerminalView` into SwiftUI, wrapped in a
/// `TerminalPanScrollView` that applies the Full/Static display mode. The
/// terminal instance is model-owned (never a fresh one) so emulator state
/// persists across view recreation; the coordinator forwards the SwiftTerm
/// delegate callbacks into the model.
struct TerminalHostView: UIViewRepresentable {
    let model: TerminalSessionModel
    let displayConfig: TerminalDisplayConfig
    let keyboardTransition: TerminalKeyboardObserver.Transition

    func makeCoordinator() -> Coordinator {
        Coordinator(model: model)
    }

    func makeUIView(context: Context) -> TerminalPanScrollView {
        let terminal = model.terminalView
        terminal.terminalDelegate = context.coordinator
        // Fire `rangeChanged` on every display update so the keyboard
        // counter-pan tracks the caret while a TUI redraws (its input box can
        // move without any scroll or size change).
        terminal.notifyUpdateChanges = true
        context.coordinator.attachGestures(to: terminal)
        let scroll = TerminalPanScrollView(terminalView: terminal, displayConfig: displayConfig)
        context.coordinator.panHost = scroll
        return scroll
    }

    func updateUIView(_ uiView: TerminalPanScrollView, context: Context) {
        uiView.displayConfig = displayConfig
        uiView.setKeyboardLift(keyboardTransition.lift, duration: keyboardTransition.duration)
    }

    /// SwiftUI does not guarantee the outgoing wrapper is dismantled before
    /// the next screen's `makeUIView` runs, so the detach is guarded: only the
    /// wrapper that still owns the shared terminal view releases it — a newer
    /// wrapper that already stole it (UIKit's `addSubview` re-parents
    /// atomically) is left untouched.
    static func dismantleUIView(_ uiView: TerminalPanScrollView, coordinator: Coordinator) {
        if uiView.terminalView.superview === uiView {
            uiView.terminalView.removeFromSuperview()
        }
    }

    @MainActor
    final class Coordinator: NSObject, @MainActor TerminalViewDelegate {
        private let model: TerminalSessionModel
        weak var panHost: TerminalPanScrollView?

        init(model: TerminalSessionModel) {
            self.model = model
        }

        /// Recognizers that target this Coordinator, and so must be rebuilt on
        /// every screen visit. Deliberately not every `mobaide.*` recognizer:
        /// the vertical-scroll pan targets the view itself, and sweeping it away
        /// here would kill terminal scrolling from the second visit onwards.
        private static let rowTapGestureName = "mobaide.rowTap"
        private static let rowDoubleTapGestureName = "mobaide.rowDoubleTap"
        private static let coordinatorGestureNames: Set<String> = [
            rowTapGestureName,
            rowDoubleTapGestureName,
        ]

        /// The recognizers live on the retained terminal view but each screen
        /// visit brings a fresh Coordinator, and a UIGestureRecognizer retains
        /// its target — so the Coordinator's own recognizers are replaced
        /// instead of kept, both re-targeting the callbacks and releasing the
        /// previous visit's Coordinator.
        func attachGestures(to view: MobaideTerminalView) {
            for recognizer in view.gestureRecognizers ?? []
            where Self.coordinatorGestureNames.contains(recognizer.name ?? "") {
                view.removeGestureRecognizer(recognizer)
            }
            let doubleTap = UITapGestureRecognizer(target: self, action: #selector(handleDoubleTap(_:)))
            doubleTap.numberOfTapsRequired = 2
            doubleTap.name = Self.rowDoubleTapGestureName
            let singleTap = UITapGestureRecognizer(target: self, action: #selector(handleSingleTap(_:)))
            singleTap.numberOfTapsRequired = 1
            singleTap.name = Self.rowTapGestureName
            singleTap.require(toFail: doubleTap)
            view.addGestureRecognizer(doubleTap)
            view.addGestureRecognizer(singleTap)
            view.installVerticalScrollGesture()
            view.neutralizeSwiftTermGestures()
            // The view outlives every visit, so the callback must not retain
            // this Coordinator: view → closure → Coordinator → model → view.
            view.onUserScroll = { [weak self] position in
                self?.userScrolled(to: position)
            }
        }

        /// The only thing that moves follow mode, mirroring Android where
        /// `onScrollPositionChanged` is fired from `doScroll` alone.
        private func userScrolled(to position: Double) {
            model.onUserScrolled(position: position)
            // The keyboard counter-pan is computed from `contentOffset`, so it
            // has to be re-evaluated whenever the user moves it.
            panHost?.caretDidMove()
        }

        @objc private func handleSingleTap(_ gesture: UITapGestureRecognizer) {
            guard let view = gesture.view as? MobaideTerminalView,
                  let (text, isCurrent) = view.resolveRow(at: gesture.location(in: view)) else { return }
            model.sendTappedRow(text: text, isCurrentInput: isCurrent)
        }

        @objc private func handleDoubleTap(_ gesture: UITapGestureRecognizer) {
            guard let view = gesture.view as? MobaideTerminalView,
                  let (text, _) = view.resolveRow(at: gesture.location(in: view)) else { return }
            model.setInput(text, requestFocus: true)
        }

        // MARK: TerminalViewDelegate

        func send(source: TerminalView, data: ArraySlice<UInt8>) {
            model.sendTerminalBytes(Data(data))
        }

        func sizeChanged(source: TerminalView, newCols: Int, newRows: Int) {
            model.resize(rows: newRows, cols: newCols)
        }

        /// Emulator-driven, fired once per line the terminal pushes past the
        /// bottom (`Terminal.scroll`) — the iOS counterpart of Android's
        /// per-update `emulator.scrollCounter` delta, and so the unread tally.
        ///
        /// It deliberately does *not* touch follow mode: `position` comes from
        /// SwiftTerm's `scrollPosition`, which is derived from the tail-pinned
        /// `yDisp` and hardcoded to `0` on the alternate buffer — so honoring it
        /// would pause follow mode every time a TUI repaints.
        func scrolled(source: TerminalView, position: Double) {
            model.onEmulatorScrolled()
            panHost?.caretDidMove()
        }

        func clipboardCopy(source: TerminalView, content: Data) {
            guard let text = String(data: content, encoding: .utf8) else { return }
            Clipboard.copy(text, isSensitive: true)
        }

        func setTerminalTitle(source: TerminalView, title: String) {}
        func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}
        func requestOpenLink(source: TerminalView, link: String, params: [String: String]) {}
        func bell(source: TerminalView) {}
        func iTermContent(source: TerminalView, content: ArraySlice<UInt8>) {}
        func rangeChanged(source: TerminalView, startY: Int, endY: Int) {
            panHost?.caretDidMove()
        }
    }
}
