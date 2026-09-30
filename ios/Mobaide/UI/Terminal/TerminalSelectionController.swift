@preconcurrency import SwiftTerm
import SwiftUI
import UIKit

/// What the SwiftUI layer needs to place the selection toolbar.
struct TerminalSelectionUi: Equatable {
    /// The selection's bounding box in the *host* (outer pan view) coordinate
    /// space, already clamped to the visible area.
    var anchor: CGRect
}

/// Terminal text selection: long-press to select a word, drag the handles to
/// extend, then Send to Input / Copy / Paste — the actions Android's
/// `TextSelectionCursorController` offers.
///
/// Selection is app-owned end to end. SwiftTerm has a complete implementation,
/// but `SelectionService` is an `internal` *class*, so none of it can be named
/// from this module, and its only public entry (`select(_:)`) depends on
/// internal state set by a long-press → deprecated `UIMenuController` flow that
/// requires first responder — which would raise the software keyboard that
/// `keyboardGate` exists to prevent.
///
/// The mode is **sticky**: while a selection is live it owns the pointer, so
/// drags adjust handles instead of scrolling. Without that, selection would be
/// dead in exactly the sessions this exists for — Claude Code and Codex run on
/// the alternate buffer with mouse reporting on, where `TerminalWheelScroll`
/// turns every drag into wheel reports.
@MainActor
final class TerminalSelectionController: NSObject {
    static let longPressGestureName = "mobaide.selectionLongPress"
    static let dragGestureName = "mobaide.selectionDrag"
    static let dismissTapGestureName = "mobaide.selectionDismissTap"

    private unowned let view: MobaideTerminalView
    private let overlay = TerminalSelectionOverlayView()
    private lazy var gestureDelegate = SelectionGestureDelegate(controller: self)

    private var longPressGesture: UILongPressGestureRecognizer?
    private var dragGesture: UIPanGestureRecognizer?
    private var dismissTapGesture: UITapGestureRecognizer?

    /// The live selection, or nil when there is none.
    private(set) var range: TerminalSelectionRange?
    /// Which handle the in-flight drag is moving.
    private var draggingHandle: TerminalSelectionHandle?
    /// The system magnifier shown while a handle is dragged, so the text under
    /// the finger stays readable.
    private var loupe: UITextLoupeSession?
    /// `Buffer.totalLinesTrimmed` as of the last emulator scroll, for the
    /// trim-shift decision.
    private var lastLinesTrimmed: Int?
    /// `yDisp` as of the last movement this controller has accounted for.
    /// Seeing it *lower* means the transcript moved underneath the selection
    /// without a scroll callback to compensate for — `handleEmulatorScroll`
    /// re-baselines it on every trim it does account for, because `yDisp` itself
    /// drops once per trimmed line whenever `Terminal.userScrolling` is set.
    private var startYDisp = 0

    /// Publishes selection geometry to the session model for the SwiftUI toolbar.
    var onChange: ((TerminalSelectionUi?) -> Void)?

    var isActive: Bool { range != nil }

    init(view: MobaideTerminalView) {
        self.view = view
        super.init()
    }

    /// Selects the word under `point` (in content coordinates) and latches the
    /// sticky mode. The long-press recognizer's only job is to call this, so
    /// tests drive the shipped path rather than a copy of it.
    ///
    /// - Returns: whether a selection started.
    @discardableResult
    func beginSelection(at point: CGPoint) -> Bool {
        guard canBeginSelection(), let word = wordRange(at: point) else { return false }
        view.cancelFling()
        // Halt any UIScrollView deceleration still in flight.
        view.setContentOffset(view.contentOffset, animated: false)
        range = word
        draggingHandle = nil
        lastLinesTrimmed = view.getTerminal().buffer.totalLinesTrimmed
        startYDisp = view.getTerminal().buffer.yDisp
        refresh()
        return true
    }

    // MARK: - Installation

    /// Idempotent, and view-owned rather than Coordinator-owned so the
    /// recognizers survive a screen revisit — the same reason
    /// `installVerticalScrollGesture` targets the view.
    ///
    /// The `mobaide.` name prefix is load-bearing: `neutralizeSwiftTermGestures`
    /// disables every non-`mobaide.` tap and long press, and re-runs on each
    /// `mouseModeChanged`, so these stay exempt even when a TUI flips mouse mode
    /// mid-selection.
    func install() {
        if longPressGesture?.view !== view {
            let press = UILongPressGestureRecognizer(target: self, action: #selector(handleLongPress(_:)))
            press.name = Self.longPressGestureName
            press.minimumPressDuration = 0.5
            press.delegate = gestureDelegate
            view.addGestureRecognizer(press)
            longPressGesture = press
        }
        if dragGesture?.view !== view {
            let drag = UIPanGestureRecognizer(target: self, action: #selector(handleDrag(_:)))
            drag.name = Self.dragGestureName
            drag.maximumNumberOfTouches = 1
            drag.delegate = gestureDelegate
            view.addGestureRecognizer(drag)
            dragGesture = drag
        }
        if dismissTapGesture?.view !== view {
            let tap = UITapGestureRecognizer(target: self, action: #selector(handleDismissTap(_:)))
            tap.name = Self.dismissTapGestureName
            tap.delegate = gestureDelegate
            view.addGestureRecognizer(tap)
            dismissTapGesture = tap
        }
        if overlay.superview !== view {
            // Behind SwiftTerm's own subviews: it removes and re-adds `caretView`
            // on every cursor move, so any ordering expressed relative to the
            // caret is unstable. Index 0 still composites over the terminal text,
            // which SwiftTerm draws into the view's own layer — hence the
            // translucent highlight.
            view.insertSubview(overlay, at: 0)
        }
    }

    /// Whether a recognizer may run while a selection is live. Everything else —
    /// the scrollback pan, the wheel pan, the row taps, the mouse-click tap — is
    /// refused for the whole sticky episode.
    func owns(_ gesture: UIGestureRecognizer) -> Bool {
        if gesture === dismissTapGesture { return true }
        if gesture === dragGesture { return grabbedHandle(for: gesture) != nil }
        return false
    }

    /// Whether a gesture may *start* a selection (nothing may, while one is live
    /// or while the drawn buffer is a frozen synchronized-output snapshot).
    func canBeginSelection() -> Bool {
        !isActive && !view.isSynchronizedOutputActive
    }

    // MARK: - Gestures

    @objc private func handleLongPress(_ gesture: UILongPressGestureRecognizer) {
        guard gesture.state == .began else { return }
        guard beginSelection(at: gesture.location(in: view)) else { return }
        // The only haptic in the flow, matching Android's single
        // `performHapticFeedback(LONG_PRESS)` at selection start.
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
    }

    @objc private func handleDrag(_ gesture: UIPanGestureRecognizer) {
        guard isActive else { return }
        switch gesture.state {
        case .began:
            draggingHandle = grabbedHandle(for: gesture)
            // Hide the toolbar so it never sits under the dragging finger
            // (Android `updateFloatingToolbarVisibility`).
            onChange?(nil)
            if draggingHandle != nil {
                loupe = UITextLoupeSession.begin(
                    at: gesture.location(in: view),
                    fromSelectionWidgetView: nil,
                    in: view
                )
            }
            dragHandle(to: gesture.location(in: view))
        case .changed:
            autoscrollIfNeeded(gesture)
            dragHandle(to: gesture.location(in: view))
        case .ended, .cancelled, .failed:
            draggingHandle = nil
            dismissLoupe()
            refresh()
        default:
            break
        }
    }

    @objc private func handleDismissTap(_ gesture: UITapGestureRecognizer) {
        guard isActive else { return }
        endSelection()
    }

    private func dragHandle(to point: CGPoint) {
        guard let handle = draggingHandle else { return }
        moveHandle(handle, to: point)
        moveLoupe(tracking: handle)
    }

    /// Follows the snapped endpoint rather than the raw touch, so the loupe
    /// shows the cell the selection actually ends on.
    private func moveLoupe(tracking handle: TerminalSelectionHandle) {
        guard let loupe, let current = range else { return }
        let caret = TerminalSelection.caretRect(for: handle, range: current, cellSize: view.cellSize)
        loupe.move(
            to: CGPoint(x: caret.midX, y: caret.midY),
            withCaretRect: caret,
            trackingCaret: true
        )
    }

    private func dismissLoupe() {
        loupe?.invalidate()
        loupe = nil
    }

    /// Re-points one handle, pinning it so it cannot cross the other.
    /// The drag recognizer's only job is to call this.
    func moveHandle(_ handle: TerminalSelectionHandle, to point: CGPoint) {
        guard let current = range,
              let position = position(at: point, snapping: handle) else { return }
        let fixed = handle == .start ? current.end : current.start
        let moved = TerminalSelectionRange(anchor: fixed, focus: position)
        range = TerminalSelection.constrain(moved, moving: handle)
        refreshOverlay()
    }

    private func autoscrollIfNeeded(_ gesture: UIPanGestureRecognizer) {
        // The alternate buffer has no scrollback to reach (Android skips
        // autoscroll there for the same reason).
        guard !view.getTerminal().isCurrentBufferAlternate else { return }
        let viewportY = gesture.location(in: view).y - view.contentOffset.y
        let rows = TerminalSelection.autoscrollRows(
            viewportY: viewportY,
            viewportHeight: view.bounds.height,
            cellHeight: view.cellSize.height
        )
        guard rows != 0 else { return }
        let target = view.contentOffset.y + CGFloat(rows) * view.cellSize.height
        let clamped = min(max(0, target), view.maxScrollOffsetY)
        guard clamped != view.contentOffset.y else { return }
        // A programmatic write, so `handleScrollViewDidScroll` ignores it and
        // follow mode is left alone — the Android rule that `setTopRow` must not
        // report a scroll position.
        view.contentOffset = CGPoint(x: view.contentOffset.x, y: clamped)
    }

    private func grabbedHandle(for gesture: UIGestureRecognizer) -> TerminalSelectionHandle? {
        guard let current = range else { return nil }
        let centers = TerminalSelection.handleCenters(
            start: current.start,
            end: current.end,
            cellSize: view.cellSize
        )
        return TerminalSelection.grabbedHandle(
            at: gesture.location(in: view),
            start: centers.start,
            end: centers.end
        )
    }

    // MARK: - Buffer reads

    /// A touch point in content coordinates → a buffer position, with the column
    /// snapped clear of any double-width glyph it landed inside. Which way it
    /// snaps depends on the endpoint being placed: see `snapToCellEnd`.
    private func position(at point: CGPoint, snapping handle: TerminalSelectionHandle = .start) -> Position? {
        let cell = view.cellSize
        guard cell.width > 0, cell.height > 0 else { return nil }
        let terminal = view.getTerminal()
        let dims = terminal.getDims()
        // Counted from the content, not from `yDisp`: `clamp` moves an
        // out-of-range row rather than refusing it, so a bound that sits above
        // the rows on screen — which `yDisp` does for as long as a fling leaves
        // it parked — would quietly relocate the touch and select text the user
        // never pointed at. Rows the lookup window cannot reach still fail
        // closed downstream, where `bufferLine` returns nil.
        let maxRow = view.lastContentRow
        let raw = Position(
            col: Int((point.x / cell.width).rounded(.down)),
            row: Int((point.y / cell.height).rounded(.down))
        )
        let clamped = TerminalSelection.clamp(raw, cols: dims.cols, maxRow: maxRow)
        guard let cells = view.selectionCells(atContentRow: clamped.row) else { return clamped }
        let snapped = handle == .end
            ? TerminalSelection.snapToCellEnd(clamped.col, cells: cells)
            : TerminalSelection.snapToCellStart(clamped.col, cells: cells)
        return Position(col: snapped, row: clamped.row)
    }

    private func wordRange(at point: CGPoint) -> TerminalSelectionRange? {
        guard let position = position(at: point),
              let cells = view.selectionCells(atContentRow: position.row) else { return nil }
        let word = TerminalSelection.expandWord(cells: cells, at: position.col)
        return TerminalSelectionRange(
            anchor: Position(col: word.lowerBound, row: position.row),
            focus: Position(col: word.upperBound, row: position.row)
        )
    }

    /// The selected text.
    ///
    /// `Terminal.getText` takes buffer-absolute positions — the same space the
    /// selection stores — and joins soft-wrapped rows without a newline while
    /// adding one on hard breaks, which is Android's `joinBackLines = true`. Its
    /// end column is *exclusive*, hence the `+ 1` off our inclusive end.
    func selectedText() -> String? {
        guard let current = range else { return nil }
        // A scrollback wipe (`ESC[3J`) trims the line list and renumbers every
        // absolute row, and `getText` neither traps nor wraps on stale input —
        // it clamps and returns whatever now sits there, so Copy would silently
        // lift unrelated lines. Two guards, because neither alone is enough:
        //
        // `bufferLine` refuses rows outside the `yDisp`-derived window, which
        // catches endpoints left *above* the shrunken transcript. It cannot
        // catch low rows: for any row < `rows` its window origin collapses to
        // 0, so row 3 keeps resolving happily to whatever line 3 now is.
        //
        // A drop in `yDisp` below the last accounted-for value is what catches
        // those — an `ESC[3J` scrollback wipe lowers it by the whole transcript
        // and reports no scroll, so nothing else compensates. Trims that *do*
        // report re-baseline this in `handleEmulatorScroll`. Buffer switches and
        // resizes — the other two ways it can drop — already cancel outright.
        guard view.getTerminal().buffer.yDisp >= startYDisp else { return nil }
        guard view.bufferLine(atContentRow: current.start.row) != nil,
              view.bufferLine(atContentRow: current.end.row) != nil else { return nil }
        let text = view.getTerminal().getText(
            start: current.start,
            end: Position(col: current.end.col + 1, row: current.end.row)
        )
        return text.isEmpty ? nil : text
    }

    // MARK: - Lifecycle

    func endSelection() {
        guard isActive else { return }
        range = nil
        draggingHandle = nil
        dismissLoupe()
        lastLinesTrimmed = nil
        lastOverlayGeometry = nil
        refresh()
        // Releasing the `holdsAutoScroll` pin does not itself move the viewport,
        // so a session that streamed while the selection was up would be left
        // stale — and with follow mode on, no unread rows accrue and the
        // jump-to-bottom button stays hidden, leaving no way back to the tail.
        // `isAutoScrollSuppressed` mirrors the model's follow-mode intent, so
        // this restores the tail exactly when the user was following. Placed
        // here rather than in the model so the two view-side exits
        // (`bufferActivated`, the per-visit reset) are covered too.
        if !view.isAutoScrollSuppressed {
            view.scrollToBottom()
        }
    }

    /// Keeps the highlight glued to its text as the emulator scrolls.
    ///
    /// A scroll inside a region (DECSTBM, and every alternate-buffer repaint)
    /// overwrites its own rows in place, so the selected text is gone rather than
    /// moved — cancel. Otherwise rows only renumber once a line is recycled off
    /// the top, which `trimShift` reads from `Buffer.totalLinesTrimmed`.
    func handleEmulatorScroll() {
        let terminal = view.getTerminal()
        let buffer = terminal.buffer
        let linesTrimmed = buffer.totalLinesTrimmed
        guard let current = range else {
            lastLinesTrimmed = linesTrimmed
            return
        }
        let scrollTop = buffer.scrollTop
        defer { lastLinesTrimmed = linesTrimmed }

        // Only a *region* scroll overwrites rows in place; the whole-screen arm
        // renumbers them instead, which `trimShift` handles. The alternate
        // buffer deliberately takes that same arm: its ring is always full and
        // `yDisp` is pinned to 0, so a scrolled line renumbers every row by one
        // exactly as `trimShift` reports. Cancelling on alt-buffer scrolls
        // instead would kill the selection on the first streamed line of a
        // Claude Code or Codex session — the case this feature exists for.
        if !TerminalFollowScroll.pushesRowIntoScrollback(scrollTop: scrollTop) {
            if TerminalSelection.scrollRegionIntersects(
                current,
                yDisp: buffer.yDisp,
                scrollTop: scrollTop,
                scrollBottom: buffer.scrollBottom
            ) {
                endSelection()
            } else {
                refreshOverlay()
            }
            return
        }

        let shift = TerminalSelection.trimShift(
            scrollTop: scrollTop,
            isAlternateBuffer: terminal.isCurrentBufferAlternate,
            previousLinesTrimmed: lastLinesTrimmed ?? linesTrimmed,
            currentLinesTrimmed: linesTrimmed
        )
        guard shift != 0 else {
            refreshOverlay()
            return
        }
        let shifted = current.shifted(rows: shift)
        // Scrolled out of the transcript entirely — Android cancels here too.
        guard shifted.start.row >= 0 else {
            endSelection()
            return
        }
        range = shifted
        // The rows this trim moved are now accounted for, so re-baseline the
        // staleness guard to match. Without this the guard reads its own
        // compensation as damage: SwiftTerm lowers `yDisp` once per trimmed line
        // while `userScrolling` is set, so a reader scrolled into history would
        // drop below `startYDisp` on the first trim and `selectedText()` would
        // refuse a selection that is in fact still correct. Re-baselining rather
        // than subtracting `shift` keeps it honest on the other arm too, where
        // rows renumber without `yDisp` moving at all.
        startYDisp = view.getTerminal().buffer.yDisp
        // `refresh`, not `refreshOverlay`: the rows moved, so the toolbar's
        // anchor moved with them and would otherwise sit a cell further from
        // the highlight on every trimmed line.
        refresh()
    }

    /// Cancels a live selection when the emulator starts drawing a frozen
    /// synchronized-output snapshot: `getText` reads the live buffer, so the
    /// highlight and the text would name different content.
    func handleSynchronizedOutputChange() {
        guard isActive, view.isSynchronizedOutputActive else { return }
        endSelection()
    }

    /// Everything `refreshOverlay` derives its geometry from. Rebuilding when
    /// none of it moved is pure waste, and the emulator hands us one `scrolled`
    /// callback per line — thousands for a single inbound batch, of which
    /// CoreAnimation would render only the last.
    private struct OverlayGeometry: Equatable {
        var origin: CGPoint
        var size: CGSize
        var start: Position
        var end: Position
        var cell: CGSize
        var cols: Int
    }

    private var lastOverlayGeometry: OverlayGeometry?

    /// Repositions the overlay after a scroll or layout change.
    func refreshOverlay() {
        let frame = CGRect(origin: view.contentOffset, size: view.bounds.size)
        guard let current = range else {
            overlay.frame = frame
            overlay.apply(rects: [], handles: nil)
            lastOverlayGeometry = nil
            return
        }
        let cell = view.cellSize
        let dims = view.getTerminal().getDims()
        let geometry = OverlayGeometry(
            origin: frame.origin,
            size: frame.size,
            start: current.start,
            end: current.end,
            cell: cell,
            cols: dims.cols
        )
        guard geometry != lastOverlayGeometry else { return }
        lastOverlayGeometry = geometry
        overlay.frame = frame
        let rects = TerminalSelection.highlightRects(
            start: current.start,
            end: current.end,
            cols: dims.cols,
            cellSize: cell
        )
        let centers = TerminalSelection.handleCenters(
            start: current.start,
            end: current.end,
            cellSize: cell
        )
        let origin = frame.origin
        let visible = CGRect(origin: .zero, size: frame.size)
        overlay.apply(
            // Clipped, not merely translated: a `CAShapeLayer` draws its path
            // outside its own bounds, so a selection spanning thousands of
            // scrollback rows would otherwise hand CoreAnimation a rect that
            // tall. The handles stay unclipped so one sitting on the viewport
            // edge is not sliced in half.
            rects: rects.compactMap {
                let local = $0.offsetBy(dx: -origin.x, dy: -origin.y)
                return local.intersects(visible) ? local.intersection(visible) : nil
            },
            handles: (
                start: centers.start.applying(.init(translationX: -origin.x, y: -origin.y)),
                end: centers.end.applying(.init(translationX: -origin.x, y: -origin.y))
            )
        )
    }

    /// Redraws and republishes the toolbar anchor.
    func refresh() {
        refreshOverlay()
        guard let current = range else {
            onChange?(nil)
            return
        }
        let cell = view.cellSize
        // The union of the drawn rects, not a single-row span: across rows
        // `end.col` is usually *left* of `start.col`, so a naive
        // `end.col - start.col` width goes negative and collapses the anchor to
        // one cell near the right edge, shoving the toolbar away from the
        // selection it acts on.
        let contentRect = TerminalSelection.highlightRects(
            start: current.start,
            end: current.end,
            cols: view.getTerminal().getDims().cols,
            cellSize: cell
        ).reduce(CGRect.null) { $0.union($1) }
        guard !contentRect.isNull else {
            onChange?(nil)
            return
        }
        // Report in the host's *viewport* space, which is what the SwiftUI
        // toolbar's `.position` uses. `convert(_:to:)` lands in the host's
        // bounds space, and the host is a scroll view whose bounds origin is
        // its contentOffset — so in Static mode a horizontal pan would displace
        // the toolbar by exactly the pan distance without this subtraction.
        let host: UIView = view.superview ?? view
        let origin = host.bounds.origin
        let anchor = view.convert(contentRect, to: host).offsetBy(dx: -origin.x, dy: -origin.y)
        let visible = CGRect(origin: .zero, size: host.bounds.size)
        guard visible.width > 0, visible.height > 0 else {
            onChange?(TerminalSelectionUi(anchor: anchor))
            return
        }
        onChange?(TerminalSelectionUi(anchor: anchor.intersects(visible) ? anchor.intersection(visible) : anchor))
    }
}

/// Highlight rects and drag handles, drawn as vector layers over the terminal.
///
/// A subview rather than a `draw(_:)` override: `TerminalView.draw` is `public`
/// (not `open`) so it cannot be overridden from this module at all, and it
/// early-returns under the Metal renderer.
///
/// The frame tracks the *visible* content window and `refreshOverlay` clips the
/// highlight rects to it, so the layers never rasterize more than one screen
/// even when a selection spans thousands of scrollback rows.
private final class TerminalSelectionOverlayView: UIView {
    private let highlightLayer = CAShapeLayer()
    private let startHandleLayer = CAShapeLayer()
    private let endHandleLayer = CAShapeLayer()

    override init(frame: CGRect) {
        super.init(frame: frame)
        // Mandatory, not hygiene: `configureAppearance` sets
        // `delaysContentTouches = false` for tap-to-stop-fling, which is only
        // sound while every content subview is non-interactive.
        isUserInteractionEnabled = false
        backgroundColor = .clear

        highlightLayer.fillColor = UIColor(MobaideColors.terminalSelectionHighlight)
            .withAlphaComponent(MobaideColors.terminalSelectionHighlightOpacity)
            .cgColor
        let handleColor = UIColor(MobaideColors.terminalSelectionHandle).cgColor
        for handle in [startHandleLayer, endHandleLayer] {
            handle.fillColor = handleColor
        }
        for sublayer in [highlightLayer, startHandleLayer, endHandleLayer] {
            sublayer.actions = ["path": NSNull(), "position": NSNull(), "hidden": NSNull()]
            layer.addSublayer(sublayer)
        }
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func apply(rects: [CGRect], handles: (start: CGPoint, end: CGPoint)?) {
        if rects.isEmpty {
            highlightLayer.path = nil
        } else {
            let path = CGMutablePath()
            for rect in rects { path.addRect(rect) }
            highlightLayer.path = path
        }
        guard let handles else {
            startHandleLayer.path = nil
            endHandleLayer.path = nil
            return
        }
        let radius = TerminalSelection.handleRadius
        startHandleLayer.path = CGPath(
            ellipseIn: CGRect(
                x: handles.start.x - radius,
                y: handles.start.y - radius,
                width: radius * 2,
                height: radius * 2
            ),
            transform: nil
        )
        endHandleLayer.path = CGPath(
            ellipseIn: CGRect(
                x: handles.end.x - radius,
                y: handles.end.y - radius,
                width: radius * 2,
                height: radius * 2
            ),
            transform: nil
        )
    }
}

/// A separate object for the same reason as `ExclusiveVerticalPanDelegate`:
/// `MobaideTerminalView` is a `UIScrollView` that is its own pan's delegate
/// through a private implementation, and `shouldRecognizeSimultaneouslyWith` is
/// an optional protocol method with no callable `super`.
@MainActor
private final class SelectionGestureDelegate: NSObject, UIGestureRecognizerDelegate {
    private unowned let controller: TerminalSelectionController

    init(controller: TerminalSelectionController) {
        self.controller = controller
    }

    func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        switch gestureRecognizer.name {
        case TerminalSelectionController.longPressGestureName:
            return controller.canBeginSelection()
        case TerminalSelectionController.dismissTapGestureName:
            // Idle, this recognizer must stay out of the way of the row taps.
            return controller.isActive
        case TerminalSelectionController.dragGestureName:
            return controller.isActive && controller.owns(gestureRecognizer)
        default:
            return true
        }
    }

    /// Selection owns the touch outright — nothing else runs alongside it.
    func gestureRecognizer(
        _ gestureRecognizer: UIGestureRecognizer,
        shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer
    ) -> Bool {
        false
    }
}
