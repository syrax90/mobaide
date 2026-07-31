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
    /// Visible row count, tracked from the `sizeChanged` delegate so row
    /// hit-testing can derive cell height without SwiftTerm's internal metrics.
    var visibleRows = 24

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
        let cellHeight = frame.height > 0 ? frame.height / CGFloat(max(1, visibleRows)) : ceil(font.lineHeight)
        guard cellHeight > 0 else { return nil }
        let viewportRow = Int((point.y - contentOffset.y) / cellHeight)
        guard viewportRow >= 0 else { return nil }
        let terminal = getTerminal()
        guard let line = terminal.getLine(row: viewportRow) else { return nil }
        guard let command = Self.promptCommand(line.translateToString(trimRight: true)) else { return nil }
        let (_, cursorY) = terminal.getCursorLocation()
        // `getCursorLocation()` is relative to the active region (yBase) while
        // `viewportRow` is relative to the scrolled viewport (yDisp); they only
        // share a frame at the bottom. So a tapped row can be the current input
        // only when following — scrolled into history, every prompt is historical.
        let isCurrentInput = scrollPosition >= 0.999 && viewportRow == cursorY
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

    /// Vertical-axis arbitration, decided per touch rather than stored. UIKit
    /// asks the view this for every recognizer attached to it, so it can refuse
    /// SwiftTerm's own scrollback pan without touching `isScrollEnabled` — which
    /// would have to be re-asserted on buffer switches that `mouseModeChanged`
    /// never reports.
    override func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        let mode = wheelScrollMode
        if gestureRecognizer === panGestureRecognizer {
            // Mouse-reporting and alternate-screen programs own the vertical
            // axis (Android `doScroll` ladder), so the scrollback pan must not
            // also run — in the alternate buffer it has nothing to scroll anyway.
            guard mode == .localScrollback else { return false }
            return super.gestureRecognizerShouldBegin(gestureRecognizer)
        }
        if gestureRecognizer === verticalScrollGesture {
            guard mode != .localScrollback,
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
            (source as? MobaideTerminalView)?.visibleRows = newRows
            model.resize(rows: newRows, cols: newCols)
        }

        func scrolled(source: TerminalView, position: Double) {
            model.onScroll(position: position)
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
