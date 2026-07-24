@preconcurrency import SwiftTerm
import SwiftUI
import UIKit

/// `TerminalView` subclass that removes the on-screen keyboard and SwiftTerm's
/// built-in input accessory. Input is driven entirely by the app's per-kind
/// `InputBar` (Android parity), so the terminal is a display + gesture surface;
/// this also removes the accessory's persistent (sticky) Ctrl toggle the issue
/// calls out — there is no ctrl-modifier surface to make one-shot.
final class MobaideTerminalView: TerminalView {
    /// Visible row count, tracked from the `sizeChanged` delegate so row
    /// hit-testing can derive cell height without SwiftTerm's internal metrics.
    var visibleRows = 24

    override var canBecomeFirstResponder: Bool { false }

    func configureAppearance() {
        nativeBackgroundColor = UIColor(MobaideColors.terminalBackground)
        nativeForegroundColor = UIColor(MobaideColors.terminalForeground)
        backgroundColor = UIColor(MobaideColors.terminalBackground)
        font = UIFont.monospacedSystemFont(ofSize: 13, weight: .regular)
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
}

/// Bridges the retained `MobaideTerminalView` into SwiftUI, wrapped in a
/// `TerminalPanScrollView` that applies the Full/Static display mode. The
/// terminal instance is model-owned (never a fresh one) so emulator state
/// persists across view recreation; the coordinator forwards the SwiftTerm
/// delegate callbacks into the model.
struct TerminalHostView: UIViewRepresentable {
    let model: TerminalSessionModel
    let displayConfig: TerminalDisplayConfig

    func makeCoordinator() -> Coordinator {
        Coordinator(model: model)
    }

    func makeUIView(context: Context) -> TerminalPanScrollView {
        let terminal = model.terminalView
        terminal.terminalDelegate = context.coordinator
        context.coordinator.attachGestures(to: terminal)
        return TerminalPanScrollView(terminalView: terminal, displayConfig: displayConfig)
    }

    func updateUIView(_ uiView: TerminalPanScrollView, context: Context) {
        uiView.displayConfig = displayConfig
    }

    @MainActor
    final class Coordinator: NSObject, @MainActor TerminalViewDelegate {
        private let model: TerminalSessionModel

        init(model: TerminalSessionModel) {
            self.model = model
        }

        func attachGestures(to view: MobaideTerminalView) {
            guard view.gestureRecognizers?.contains(where: { $0.name == "mobaide.rowTap" }) != true else { return }
            let doubleTap = UITapGestureRecognizer(target: self, action: #selector(handleDoubleTap(_:)))
            doubleTap.numberOfTapsRequired = 2
            doubleTap.name = "mobaide.rowDoubleTap"
            let singleTap = UITapGestureRecognizer(target: self, action: #selector(handleSingleTap(_:)))
            singleTap.numberOfTapsRequired = 1
            singleTap.name = "mobaide.rowTap"
            singleTap.require(toFail: doubleTap)
            view.addGestureRecognizer(doubleTap)
            view.addGestureRecognizer(singleTap)
            Self.disableSelectionGestures(on: view)
        }

        /// SwiftTerm activates an any-direction selection pan lazily from its own
        /// double-tap, triple-tap, and long-press. Disabling those keeps a
        /// horizontal pan from being hijacked into text selection; input is
        /// driven by the InputBar and row taps, so terminal-side selection is
        /// unused. The app's own `mobaide.*` taps are left intact.
        private static func disableSelectionGestures(on view: MobaideTerminalView) {
            for recognizer in view.gestureRecognizers ?? [] where recognizer.name?.hasPrefix("mobaide.") != true {
                if recognizer is UILongPressGestureRecognizer {
                    recognizer.isEnabled = false
                } else if let tap = recognizer as? UITapGestureRecognizer, tap.numberOfTapsRequired >= 2 {
                    recognizer.isEnabled = false
                }
            }
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
            model.sendBytes(Data(data))
        }

        func sizeChanged(source: TerminalView, newCols: Int, newRows: Int) {
            (source as? MobaideTerminalView)?.visibleRows = newRows
            model.resize(rows: newRows, cols: newCols)
        }

        func scrolled(source: TerminalView, position: Double) {
            model.onScroll(position: position)
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
        func rangeChanged(source: TerminalView, startY: Int, endY: Int) {}
    }
}
