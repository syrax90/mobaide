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

    /// Puts a raised keyboard away. Set by the Coordinator rather than called
    /// on the model directly: the view is model-owned, so reaching back would
    /// retain it (cf. `onUserScroll`).
    var onDismissSoftKeyboard: (() -> Void)?

    /// Thresholded, never `> 0`: a hardware keyboard leaves a bottom-anchored
    /// full-width assistant bar that `lift` reports as ~35, and treating that as
    /// covering would resign the responder on every touch and end hardware typing.
    var isSoftKeyboardCovering: Bool {
        (superview as? TerminalPanScrollView).map {
            $0.keyboardLift > TerminalKeyboardPan.minimumCoveringLift
        } ?? false
    }

    /// Latched at touch-down because it cannot be re-read later: `mobaide.rowTap`
    /// waits out the double-tap timeout, by which point `keyboardWillHide` has
    /// already dropped the lift — a late read would let that tap through and send a
    /// bare CR.
    private(set) var dismissedKeyboardOnCurrentTouch = false

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
        // Tap-to-stop has to kill a fling on touch-down, not 150ms later. The
        // delay only ever shielded interactive content subviews, and SwiftTerm
        // gives this scroll view none — caret, Metal layer and progress bar all
        // set `isUserInteractionEnabled = false`.
        delaysContentTouches = false
        installVerticalScrollGesture()
        selectionController.install()
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

    // MARK: - Text selection

    lazy var selectionController = TerminalSelectionController(view: self)

    var isSelectionActive: Bool { selectionController.isActive }

    /// The buffer line rendered at absolute content row `row`, or nil when
    /// nothing is drawn there.
    ///
    /// `getLine(row:)` counts from `buffer.yDisp`, but the iOS renderer counts
    /// from `contentOffset`, and the two only agree while following: SwiftTerm
    /// moves `yDisp` for a drag it is tracking and then leaves it at the row the
    /// finger lifted on, so momentum and every programmatic write walk the
    /// renderer past it. `yDisp` is public and `getLine` is a synchronous pure
    /// read, so the window is re-pointed at the row being looked up and restored
    /// before any SwiftTerm code can observe it.
    ///
    /// The equality holds against the rows on screen. While a program holds
    /// synchronized output (DEC mode 2026) SwiftTerm keeps parsing into that
    /// same buffer but stops painting it, so `getLine` outruns the glass — both
    /// callers refuse for the duration instead, see `TerminalSynchronizedOutput`.
    func bufferLine(atContentRow row: Int) -> BufferLine? {
        let terminal = getTerminal()
        let buffer = terminal.buffer
        let restore = buffer.yDisp
        let rows = terminal.getDims().rows
        // `getLine` addresses exactly `rows` lines from the window origin, so an
        // origin a fraction of a cell above the tail — where `scrollToBottom()`
        // parks — would push the bottom-most line out of reach. Raise the window
        // just far enough to cover the wanted row, but never past `yDisp`:
        // beyond it `row + yDisp` runs off the end of `lines`, and
        // `CircularList` wraps instead of trapping, so an `ESC[3J` scrollback
        // wipe (which leaves `contentSize` stale) could otherwise resolve a
        // recycled line.
        let topRow = min(max(0, row - rows + 1), restore)
        let local = row - topRow
        guard local >= 0, local < rows else { return nil }
        guard topRow != restore else { return terminal.getLine(row: local) }
        buffer.yDisp = topRow
        defer { buffer.yDisp = restore }
        return terminal.getLine(row: local)
    }

    /// One entry per terminal column of `row`, for word expansion and
    /// wide-glyph snapping. `getCharacter(for:)` rather than
    /// `CharData.getCharacter()` so extended grapheme clusters resolve.
    func selectionCells(atContentRow row: Int) -> [TerminalSelection.Cell]? {
        guard let line = bufferLine(atContentRow: row) else { return nil }
        let terminal = getTerminal()
        let cols = min(terminal.getDims().cols, line.count)
        guard cols > 0 else { return nil }
        return (0..<cols).map { column in
            TerminalSelection.Cell(
                character: terminal.getCharacter(for: line[column]),
                width: line.getWidth(index: column)
            )
        }
    }

    /// Monotonic clock behind the synchronized-output deadline. Same
    /// `mach_absolute_time` family as the `DispatchTime` SwiftTerm schedules its
    /// own expiry against, so the two pause and resume together when the app is
    /// suspended.
    var synchronizedOutputNow: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }

    private var synchronizedOutput = TerminalSynchronizedOutput()

    /// Whether the emulator may still be drawing a synchronized-output snapshot
    /// rather than the buffer `resolveRow` reads.
    var isSynchronizedOutputActive: Bool {
        synchronizedOutput.isActive(at: synchronizedOutputNow())
    }

    /// The only tracked way to hand emulator output to SwiftTerm.
    ///
    /// The inherited `feed(byteArray:)` and `feed(text:)` are declared in an
    /// extension, so they are statically dispatched and cannot be overridden —
    /// nothing in production may reach the emulator through them, or the row
    /// lookup loses track of what is actually being drawn.
    func feedTrackingSynchronizedOutput(_ bytes: ArraySlice<UInt8>) {
        feed(byteArray: bytes)
        synchronizedOutput.scan(bytes, at: synchronizedOutputNow())
        // The renderer is now drawing a frozen snapshot while `getText` still
        // reads the live buffer, so the highlight and the text it would yield
        // name different content — drop the selection rather than lie.
        selectionController.handleSynchronizedOutputChange()
    }

    /// Resolves a tap point (in this view's content coordinates) to a prompt
    /// row's command text and whether that row holds the current input. Returns
    /// nil for rows without a recognizable prompt prefix so taps on plain
    /// output never execute anything.
    func resolveRow(at point: CGPoint) -> (text: String, isCurrentInput: Bool)? {
        // While the emulator holds a synchronized-output snapshot the drawn row
        // is not the row `getLine` returns, and the drawn one is unreachable —
        // so resolve nothing rather than a line the user never saw. This also
        // covers the tap ladder reading a live `mouseMode` and
        // `isCurrentBufferAlternate` the renderer has frozen past.
        guard !isSynchronizedOutputActive else { return nil }
        let cellHeight = cellSize.height
        guard cellHeight > 0, point.y >= 0 else { return nil }
        // A single floor over the absolute content position. Flooring the
        // viewport row and the window origin separately drops a row whenever
        // the offset is not cell-aligned — which a drag, and the sub-cell
        // remainder `scrollToBottom()` lands on, both make the normal case.
        let contentRow = Int(point.y / cellHeight)
        let terminal = getTerminal()
        // Reached only on the `isScrolledToBottom` arm below, which is what
        // makes `yDisp` equal to `yBase` here — the first row of the active
        // region as well as the largest legal scrollback window origin. It is
        // not an invariant of `yDisp` itself, which parks below `yBase` for as
        // long as a drag holds `Terminal.userScrolling`.
        let activeRegionTop = terminal.buffer.yDisp
        guard let line = bufferLine(atContentRow: contentRow) else { return nil }
        guard let command = Self.promptCommand(line.translateToString(trimRight: true)) else { return nil }
        let (_, cursorY) = terminal.getCursorLocation()
        // `getCursorLocation()` is relative to the active region, so the tapped
        // row is the live input only when it is that region's cursor row — and
        // only while following: scrolled into history every prompt is historical.
        let isCurrentInput = isScrolledToBottom && contentRow - activeRegionTop == cursorY
        return (command, isCurrentInput)
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

    /// Swapping the screen buffer renumbers every row: the alternate buffer runs
    /// at `yDisp == 0` over `rows` lines while the normal one can sit thousands
    /// deep. A selection's rows are content-absolute, so they would silently
    /// come to name other content — and `getText` would hand Copy that text.
    /// No scroll callback fires for a buffer swap, so this is the only hook.
    override func bufferActivated(source: Terminal) {
        super.bufferActivated(source: source)
        selectionController.endSelection()
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
        // Sticky selection owns the pointer for the whole episode. One gate
        // suspends the scrollback pan, the wheel/cursor-key ladder (and so the
        // fling), both row taps and the mouse-click tap — including any
        // recognizer SwiftTerm adds later. Without it, extending a selection by
        // dragging would lose arbitration to the wheel ladder in exactly the
        // mouse-reporting TUIs this feature exists for.
        if selectionController.isActive {
            return selectionController.owns(gestureRecognizer)
        }
        // The touch that dismissed the keyboard is spent: it must not also
        // resend a prompt row, click into a mouse-tracking TUI, or copy to the
        // composer. Refusing taps wholesale rather than by name is exact —
        // `neutralizeSwiftTermGestures()` disables every non-`mobaide.` tap and
        // the selection tap is owned by the branch above, leaving precisely the
        // three action taps, plus any added later.
        if dismissedKeyboardOnCurrentTouch, gestureRecognizer is UITapGestureRecognizer {
            return false
        }
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
            // `touchesBegan` already stopped any momentum; this covers a pan
            // that begins from a touch the view never saw.
            cancelFling()
            wheelAccumulator.reset()
            wheelAnchor = gesture.location(in: self)
            // Consume the slop travel too, matching Android's GestureDetector
            // whose first `onScroll` already includes it.
            consumeVerticalScroll(gesture)
        case .changed:
            consumeVerticalScroll(gesture)
        case .ended:
            wheelAccumulator.reset()
            startFling(velocity: gesture.velocity(in: self))
        case .cancelled, .failed:
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
            batchingEmulatorWrites {
                for _ in 0 ..< steps {
                    terminal.sendEvent(buttonFlags: flags, x: cell.col, y: cell.row)
                }
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

    private var emulatorWriteBatch: [UInt8]?

    /// Collects everything the emulator writes back during `body` and delivers
    /// it as a single write, so a burst of wheel reports costs one WebSocket
    /// frame instead of one per row — the wheel arm's counterpart of the
    /// concatenation the cursor-key arm does inline.
    ///
    /// `send(source:data:)` also carries the emulator's automatic replies
    /// (DA, DSR), but only a synchronous run of `sendEvent` is ever wrapped
    /// here and nothing feeds the emulator inside one, so no reply can be
    /// swallowed into a batch.
    private func batchingEmulatorWrites(_ body: () -> Void) {
        emulatorWriteBatch = []
        body()
        let batch = emulatorWriteBatch ?? []
        emulatorWriteBatch = nil
        guard !batch.isEmpty else { return }
        // The un-batched exit, deliberately not `send(data:)` — that one adds
        // `ensureCaretIsVisible()`, a snap-to-caret the per-row path never ran.
        super.send(source: getTerminal(), data: batch[...])
    }

    override func send(source: Terminal, data: ArraySlice<UInt8>) {
        guard emulatorWriteBatch != nil else {
            super.send(source: source, data: data)
            return
        }
        emulatorWriteBatch?.append(contentsOf: data)
    }

    // MARK: - Fling

    private(set) var flingTask: Task<Void, Never>?

    /// Momentum after the finger lifts, Android `onFling`.
    ///
    /// The ladder arm is snapshotted rather than re-derived per frame: a
    /// program that leaves the alternate screen or drops mouse reporting
    /// mid-fling must have the remaining rows dropped, not re-routed onto
    /// whatever replaced it (Android `mouseTrackingAtStartOfFling`).
    func startFling(velocity: CGPoint) {
        cancelFling()
        let arm = wheelScrollMode
        // `.localScrollback` needs nothing: there the touch belongs to
        // SwiftTerm's own scroll view, which decelerates by itself.
        guard arm != .localScrollback else { return }
        let steps = TerminalWheelScroll.flingSteps(
            rows: TerminalWheelScroll.flingRows(
                velocityY: velocity.y,
                cellHeight: cellSize.height,
                rows: getTerminal().getDims().rows
            )
        )
        guard !steps.isEmpty else { return }
        let anchor = wheelAnchor
        flingTask = Task { [weak self] in
            for step in steps {
                try? await Task.sleep(for: TerminalWheelScroll.flingFrameInterval)
                guard !Task.isCancelled, let self, self.wheelScrollMode == arm else { return }
                self.emitVerticalScroll(rowsDown: step, at: anchor)
            }
        }
    }

    func cancelFling() {
        flingTask?.cancel()
        flingTask = nil
    }

    /// Tap-to-stop, the `UIScrollView` convention the outer
    /// `TerminalPanScrollView` already honors for its own fling. It has to hang
    /// off the touch rather than off a recognizer: `TerminalWheelScroll.claimsPan`
    /// refuses a zero sample, so a stationary tap never reaches the pan at all.
    ///
    /// Deliberately not Android, which has no tap-to-stop — its `onDown`
    /// returns false and a fling always runs to completion.
    ///
    /// Also the single point where touching the terminal puts a raised keyboard
    /// away: a tap and the start of a scroll drag both pass through here, and it
    /// runs before any recognizer can be asked to begin — which is what makes
    /// the decision latchable.
    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        cancelFling()
        dismissedKeyboardOnCurrentTouch = isSoftKeyboardCovering
        if dismissedKeyboardOnCurrentTouch {
            // Latches first: arming the ladder's fail-closed guards before any
            // recognizer is touched leaves no window in which releasing a
            // failure dependency could resolve a row against stale readings.
            onDismissSoftKeyboard?()
            spendSingleTapRecognizers()
        }
        super.touchesBegan(touches, with: event)
    }

    /// Takes the dismissing touch away from the taps that could act on it alone,
    /// instead of only refusing them once they ask to begin. Disabling cancels a
    /// recognizer's in-flight tracking and re-enabling arms it for the next
    /// touch only.
    ///
    /// Needed because `mobaide.rowTap` waits out `mobaide.rowDoubleTap` and so
    /// is still pending ~300ms after this touch lifts — long enough for a
    /// following touch-down to recompute the latch to false and let the spent
    /// tap resolve after all, as a row resend.
    ///
    /// Multi-tap recognizers are deliberately left armed: cancelling
    /// `mobaide.rowDoubleTap` resets its tally, so the second half of a
    /// double-tap-to-copy would arrive as a fresh single tap and *execute* the
    /// row it was meant to copy. Letting the pair complete keeps the worst case
    /// at copying text into the composer.
    ///
    /// Skipped entirely while a selection is live: that episode already owns
    /// every tap through `gestureRecognizerShouldBegin`, so nothing needs
    /// spending, and cancelling would strand the selection and its toolbar.
    ///
    /// The `isEnabled` filter is load-bearing: `neutralizeSwiftTermGestures()`
    /// parks SwiftTerm's own taps disabled, and re-enabling one would hand a
    /// terminal touch back the `becomeFirstResponder()` `keyboardGate` forbids.
    private func spendSingleTapRecognizers() {
        guard !selectionController.isActive else { return }
        for recognizer in gestureRecognizers ?? [] {
            guard let tap = recognizer as? UITapGestureRecognizer,
                  tap.isEnabled,
                  tap.numberOfTapsRequired == 1 else { continue }
            tap.isEnabled = false
            tap.isEnabled = true
        }
    }

    /// The terminal view is model-owned and outlives the screen, so a fling
    /// left running would keep writing to a session the user has left.
    override func willMove(toWindow newWindow: UIWindow?) {
        if newWindow == nil { cancelFling() }
        super.willMove(toWindow: newWindow)
    }

    // MARK: - Pointer reporting

    /// Reports a tap to a mouse-tracking program so the click lands on the cell
    /// the user touched — which is how Claude Code, vim and htop move their own
    /// cursor — instead of the tap being resolved into a row the app submits.
    ///
    /// SwiftTerm's own single tap does this, but it is disabled by
    /// `neutralizeSwiftTermGestures()` and reports only while the view is first
    /// responder, which `keyboardGate` deliberately prevents. Android has always
    /// sent the click from its `onUp` fast path.
    ///
    /// Press and release are batched into one write: they are a single gesture,
    /// and a program that repaints on the press would otherwise see the release
    /// a WebSocket round trip later.
    ///
    /// - Returns: true when the tap was reported to the program.
    @discardableResult
    func sendMouseClick(at point: CGPoint) -> Bool {
        let terminal = getTerminal()
        guard TerminalWheelScroll.reportsClick(terminal.mouseMode) else { return false }
        let dims = terminal.getDims()
        let cell = TerminalWheelScroll.cell(
            at: point,
            contentOffset: contentOffset,
            cellSize: cellSize,
            cols: dims.cols,
            rows: dims.rows
        )
        batchingEmulatorWrites {
            for release in [false, true] {
                // Button 0 is the left button. SwiftTerm's own `encodeFlags`
                // hardcodes button 1 — the middle button, which repositions nothing.
                let flags = terminal.encodeButton(
                    button: 0,
                    release: release,
                    shift: false,
                    meta: false,
                    control: false
                )
                terminal.sendEvent(buttonFlags: flags, x: cell.col, y: cell.row)
            }
        }
        return true
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

    /// Whether the viewport is pinned. A live selection adds a second,
    /// self-restoring term so incoming output cannot drag the highlight away
    /// mid-drag (Android suppresses its auto-scroll while selecting for the same
    /// reason). Kept separate from `isAutoScrollSuppressed`, whose sole writer is
    /// the model's follow-mode sync — so leaving selection restores the model's
    /// intent with no bookkeeping.
    var holdsAutoScroll: Bool { isAutoScrollSuppressed || isSelectionActive }

    /// True while PTY bytes are being handed to the emulator. SwiftTerm's
    /// snap-to-tail runs synchronously inside that call (`Terminal.scroll` →
    /// `scrolled(source:yDisp:)` → `updateScroller()`), and this is the only way
    /// to tell it apart from a genuine drag: UIKit reports both as a scroll of
    /// the same scroll view, so a line that lands mid-gesture would otherwise
    /// read as the user returning to the tail and silently resume follow mode.
    private(set) var isApplyingEmulatorOutput = false

    /// True while the bytes being fed are the server's ring *replay* rather than live output.
    /// The ring is replayed verbatim on every attach, so a control whose effect lands outside
    /// the screen — OSC 52, which writes the system pasteboard — would otherwise re-fire every
    /// time the session is opened.
    private(set) var isApplyingReplayOutput = false

    /// Feed PTY bytes to the emulator, attributing any offset move they cause.
    ///
    /// `replayed` carries no default on purpose, matching the required parameter on Android's
    /// `TerminalSession.emitOutput`: a caller that omitted it would silently claim "live", which
    /// is the fail-open direction — and the trailing-closure spelling would hide the omission.
    func applyingEmulatorOutput(replayed: Bool, _ body: () -> Void) {
        isApplyingEmulatorOutput = true
        isApplyingReplayOutput = replayed
        defer {
            isApplyingEmulatorOutput = false
            isApplyingReplayOutput = false
        }
        body()
    }

    var maxScrollOffsetY: CGFloat {
        TerminalFollowScroll.maxOffsetY(contentHeight: contentSize.height, viewportHeight: bounds.height)
    }

    /// Highest content row the buffer actually holds, counted from the scroll
    /// view's own geometry rather than from `yDisp`.
    ///
    /// `contentSize` is the one row count that stays true while `yDisp` is
    /// parked: SwiftTerm's `updateScroller()` writes it *above* its
    /// `isTracking || (userScrolling && isDecelerating)` early return, precisely
    /// so rows appended during a drag stay reachable. A bound derived from
    /// `yDisp` instead goes stale the moment a fling leaves it at the row the
    /// finger lifted on, and clamping a touch against a stale bound does not
    /// reject the touch — it silently moves it to a row the user never put a
    /// finger on, which is how a selection ends up copying text nobody chose.
    /// The floor is a full viewport, not zero: `contentSize` stays `.zero` until
    /// SwiftTerm's `updateScroller()` first runs, and it runs only on a scroll —
    /// so a session whose output has not yet filled a screen has never set it.
    /// The buffer always holds at least a viewport's worth of lines, and falling
    /// back to zero would clamp every touch below the first row onto it.
    var lastContentRow: Int {
        let rows = getTerminal().getDims().rows
        let cellHeight = cellSize.height
        let counted = cellHeight > 0 ? Int(contentSize.height / cellHeight) : 0
        return max(counted, rows, 1) - 1
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
            guard holdsAutoScroll,
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

    /// Replaces SwiftTerm's `scroll(toPosition: 1.0)`, which cannot be relied on
    /// here: it routes through `scrollTo(row:)`, whose body is guarded by
    /// `row != yDisp`, so it does nothing whenever `yDisp` already names the
    /// target row — which is every jump-to-bottom that did not come from a drag
    /// SwiftTerm itself tracked.
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
        // The overlay tracks the visible content window, so it has to follow
        // every offset move — programmatic ones included.
        if isSelectionActive { selectionController.refreshOverlay() }
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

/// Lets the immediate click tap coexist with the delayed row tap and the double
/// tap, rather than racing them.
///
/// UIKit consults this only when recognition by one would otherwise *block* the
/// other, and a `true` from either side is documented to guarantee simultaneous
/// recognition. Two single-tap recognizers on one view are observed to both
/// fire, so this is a no-op in practice — but the click tap deliberately has no
/// `require(toFail:)`, and it always recognizes first, so without an explicit
/// answer the plain-shell row tap and the double-tap copy-to-composer would be
/// left depending on that unwritten default.
@MainActor
private final class SimultaneousTapDelegate: NSObject, UIGestureRecognizerDelegate {
    func gestureRecognizer(
        _ gestureRecognizer: UIGestureRecognizer,
        shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer
    ) -> Bool {
        otherGestureRecognizer is UITapGestureRecognizer
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
        private let simultaneousTapDelegate = SimultaneousTapDelegate()

        /// Which arm of the tap ladder owned the touch when it lifted.
        ///
        /// The click fires at touch-up but the row tap only after the double-tap
        /// timeout, and the state the ladder reads is written by the program in
        /// the PTY — which the click itself tells that a tap just happened. A
        /// program could answer it by dropping mouse mode and painting a fake
        /// prompt on the tapped row, turning the delayed tap into an `execute`
        /// frame (Ctrl+C, retype, CR) it chose the text for. Latching at lift and
        /// requiring both evaluations to agree makes the gate fail closed; it
        /// also covers the benign case of a TUI simply exiting inside the window.
        private var lastTapOwnedByProgram = false

        /// Whether the emulator was drawing a synchronized-output snapshot when the
        /// touch lifted.
        ///
        /// `resolveRow` re-reads that state, but a program can clear mode 2026 in
        /// the gap before the delayed row tap — leaving the row it swapped in both
        /// drawn and resolvable by the time the gate runs. The reading that matches
        /// what the user saw is the one taken at lift, so as with
        /// `lastTapOwnedByProgram` both evaluations have to agree.
        private var lastTapDuringFrozenDisplay = false

        init(model: TerminalSessionModel) {
            self.model = model
        }

        /// Recognizers that target this Coordinator, and so must be rebuilt on
        /// every screen visit. Deliberately not every `mobaide.*` recognizer:
        /// the vertical-scroll pan targets the view itself, and sweeping it away
        /// here would kill terminal scrolling from the second visit onwards.
        private static let rowTapGestureName = "mobaide.rowTap"
        private static let rowDoubleTapGestureName = "mobaide.rowDoubleTap"
        private static let mouseTapGestureName = "mobaide.mouseTap"
        private static let coordinatorGestureNames: Set<String> = [
            rowTapGestureName,
            rowDoubleTapGestureName,
            mouseTapGestureName,
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
            // Deliberately not waiting on `doubleTap`: a click is a pointer event
            // and must land as promptly as Android's `onUp` fast path sends it.
            // The two tap handlers are mutually exclusive by mouse mode, so only
            // one of them ever acts on a given tap.
            let mouseTap = UITapGestureRecognizer(target: self, action: #selector(handleMouseTap(_:)))
            mouseTap.numberOfTapsRequired = 1
            mouseTap.name = Self.mouseTapGestureName
            mouseTap.delegate = simultaneousTapDelegate
            view.addGestureRecognizer(doubleTap)
            view.addGestureRecognizer(singleTap)
            view.addGestureRecognizer(mouseTap)
            view.installVerticalScrollGesture()
            view.selectionController.install()
            view.neutralizeSwiftTermGestures()
            // The view outlives every visit, so the callback must not retain
            // this Coordinator: view → closure → Coordinator → model → view.
            view.onUserScroll = { [weak self] position in
                self?.userScrolled(to: position)
            }
            view.onDismissSoftKeyboard = { [weak self] in
                guard let self else { return }
                // The dismissing touch's `mouseTap` is spent, so neither latch
                // takes its lift-time reading this touch. A program that repaints a
                // fake prompt behind a synchronized-output freeze must not have a
                // stale `false` resolve the row for it.
                lastTapOwnedByProgram = true
                lastTapDuringFrozenDisplay = true
                model.dismissSoftKeyboard()
            }
            view.selectionController.onChange = { [weak self] selection in
                self?.model.selectionUi = selection
            }
            // A selection is per-visit UI, not session state: leaving the screen
            // with one live would strand the toolbar on the next visit.
            view.selectionController.endSelection()
        }

        /// The only thing that moves follow mode, mirroring Android where
        /// `onScrollPositionChanged` is fired from `doScroll` alone.
        private func userScrolled(to position: Double) {
            model.onUserScrolled(position: position)
            // The keyboard counter-pan is computed from `contentOffset`, so it
            // has to be re-evaluated whenever the user moves it.
            panHost?.caretDidMove()
        }

        @objc private func handleMouseTap(_ gesture: UITapGestureRecognizer) {
            guard let view = gesture.view as? MobaideTerminalView else { return }
            lastTapOwnedByProgram = view.wheelScrollMode != .localScrollback
            // This recognizer fires at lift; the row tap only arrives a double-tap
            // timeout later, by which point the program can have released the
            // freeze and left the row it swapped in both drawn and resolvable.
            // What the user decided against is the screen at lift.
            lastTapDuringFrozenDisplay = view.isSynchronizedOutputActive
            view.sendMouseClick(at: gesture.location(in: view))
        }

        /// Row-tap submit is a shell affordance: it only applies where the app
        /// owns the pointer. A mouse-tracking program got the tap as a click
        /// instead, and on a bare alternate screen the rows are the program's own
        /// chrome — submitting one would Ctrl+C it and retype the text.
        @objc private func handleSingleTap(_ gesture: UITapGestureRecognizer) {
            guard let view = gesture.view as? MobaideTerminalView,
                  !lastTapOwnedByProgram,
                  !lastTapDuringFrozenDisplay,
                  view.wheelScrollMode == .localScrollback,
                  let (text, isCurrent) = view.resolveRow(at: gesture.location(in: view)) else { return }
            model.sendTappedRow(text: text, isCurrentInput: isCurrent)
        }

        @objc private func handleDoubleTap(_ gesture: UITapGestureRecognizer) {
            guard let view = gesture.view as? MobaideTerminalView,
                  !lastTapDuringFrozenDisplay,
                  let (text, _) = view.resolveRow(at: gesture.location(in: view)) else { return }
            model.setInput(text, requestFocus: true)
        }

        // MARK: TerminalViewDelegate

        func send(source: TerminalView, data: ArraySlice<UInt8>) {
            model.sendTerminalBytes(Data(data))
        }

        func sizeChanged(source: TerminalView, newCols: Int, newRows: Int) {
            // A new grid reflows the buffer, so the selection's row/column
            // coordinates no longer name the text they were taken from.
            model.endSelection()
            model.resize(rows: newRows, cols: newCols)
        }

        /// Emulator-driven, fired at the end of every `Terminal.scroll` — the
        /// iOS counterpart of Android's per-update `emulator.scrollCounter`
        /// delta, and so the unread tally. Only scrolls that reached the
        /// scrollback feed it, apart from the narrow-margin arm the predicate
        /// cannot see; the caret moved either way.
        ///
        /// It deliberately does *not* touch follow mode: `position` comes from
        /// SwiftTerm's `scrollPosition`, which is derived from the tail-pinned
        /// `yDisp` and hardcoded to `0` on the alternate buffer — so honoring it
        /// would pause follow mode every time a TUI repaints.
        func scrolled(source: TerminalView, position: Double) {
            if TerminalFollowScroll.pushesRowIntoScrollback(
                scrollTop: source.getTerminal().buffer.scrollTop
            ) {
                model.onEmulatorScrolled()
            }
            // Keeps a live selection glued to its text, or cancels it when the
            // text is overwritten rather than moved.
            if let terminal = source as? MobaideTerminalView {
                terminal.selectionController.handleEmulatorScroll()
            }
            panHost?.caretDidMove()
        }

        /// OSC 52 *write*. The user's own Copy does not come through here — it calls
        /// `Clipboard.copy` directly from the model — so this delegate is the PTY's channel
        /// alone, and bytes the server replayed are refused: the ring is replayed verbatim on
        /// every attach, so one yank would otherwise re-copy on every session open.
        /// Both guards fail *closed*: an unrecognised source view refuses the copy rather than
        /// waving it through, so rewiring the delegate can only disable the feature, never the gate.
        func clipboardCopy(source: TerminalView, content: Data) {
            guard let terminal = source as? MobaideTerminalView else { return }
            guard !terminal.isApplyingReplayOutput else { return }
            guard let text = String(data: content, encoding: .utf8) else { return }
            Clipboard.copy(text, isSensitive: true)
        }

        /// OSC 52 *read* (`ESC]52;c;?ST`) — refused, always. SwiftTerm 1.15
        /// added the query direction and base64s whatever this returns straight
        /// back to the program, so answering would let anything running on the
        /// agent host lift the user's pasteboard across the relay. The write
        /// direction above stays allowed because it only moves outward data the
        /// program already holds.
        ///
        /// SwiftTerm's own `TerminalViewDelegate` extension already defaults to
        /// `nil`, so this override is not needed to compile — it is here so the
        /// refusal is this app's decision and survives an upstream default that
        /// ever stops denying. Deleting it would hand that choice back to a
        /// dependency.
        func clipboardRead(source: TerminalView) -> Data? { nil }

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
