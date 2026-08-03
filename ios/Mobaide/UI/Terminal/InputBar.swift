import SwiftUI

/// A control key sent verbatim as an `i` frame (Android `InputBar.ControlKey`);
/// the byte sequences are load-bearing and must match the agent's expectations.
struct TerminalControlKey: Identifiable {
    let id: String
    let label: String
    let bytes: [UInt8]
    var systemImage: String?

    static let esc = TerminalControlKey(id: "esc", label: "Esc", bytes: [0x1B])
    static let tab = TerminalControlKey(id: "tab", label: "Tab", bytes: [0x09])
    /// DEL (0x7f) — the terminal erase byte readline/Claude Code/Codex expect
    /// (not 0x08 BS). Terminal-surface typing has no composer buffer to delete
    /// from, so an explicit erase key is the reliable path.
    static let backspace = TerminalControlKey(id: "backspace", label: "Backspace", bytes: [0x7F], systemImage: "delete.left")
    static let enter = TerminalControlKey(id: "enter", label: "Enter", bytes: [0x0D])
    static let ctrlC = TerminalControlKey(id: "ctrlC", label: "Ctrl+C", bytes: [0x03])
    static let up = TerminalControlKey(id: "up", label: "Up", bytes: [0x1B, 0x5B, 0x41], systemImage: "arrow.up")
    static let down = TerminalControlKey(id: "down", label: "Down", bytes: [0x1B, 0x5B, 0x42], systemImage: "arrow.down")
    static let left = TerminalControlKey(id: "left", label: "Left", bytes: [0x1B, 0x5B, 0x44], systemImage: "arrow.left")
    static let right = TerminalControlKey(id: "right", label: "Right", bytes: [0x1B, 0x5B, 0x43], systemImage: "arrow.right")
    static let ctrlO = TerminalControlKey(id: "ctrlO", label: "Ctrl+O", bytes: [0x0F])
    static let ctrlR = TerminalControlKey(id: "ctrlR", label: "Ctrl+R", bytes: [0x12])
    static let ctrlL = TerminalControlKey(id: "ctrlL", label: "Ctrl+L", bytes: [0x0C])
    static let mode = TerminalControlKey(id: "mode", label: "Mode", bytes: [0x1B, 0x5B, 0x5A])
    static let rewind = TerminalControlKey(id: "rewind", label: "Rewind", bytes: [0x1B, 0x1B])
    static let approve = TerminalControlKey(id: "approve", label: "Approve", bytes: [0x79])
    static let reject = TerminalControlKey(id: "reject", label: "Reject", bytes: [0x6E])
    static let ctrlK = TerminalControlKey(id: "ctrlK", label: "Ctrl+K", bytes: [0x0B])
    static let ctrlG = TerminalControlKey(id: "ctrlG", label: "Ctrl+G", bytes: [0x07])
    static let ctrlD = TerminalControlKey(id: "ctrlD", label: "Ctrl+D", bytes: [0x04])

    /// Canonical left-to-right order for every control key (Android `CONTROL_KEY_ORDER`).
    /// Each kind renders only the subset it supports, filtered from this single list, so the
    /// button order stays consistent across kinds — only which buttons appear differs.
    static let controlKeyOrder: [TerminalControlKey] = [
        enter, up, down, left, right, ctrlC, mode, ctrlL, esc,
        approve, reject, ctrlK, ctrlG, ctrlD, rewind, tab, backspace, ctrlO, ctrlR,
    ]

    /// Reorder a set of keys into the canonical layout (Android `orderedControlKeys`).
    /// Membership is defined by the caller; position is always dictated by `controlKeyOrder`.
    private static func ordered(_ keys: [TerminalControlKey]) -> [TerminalControlKey] {
        let present = Set(keys.map(\.id))
        return controlKeyOrder.filter { present.contains($0.id) }
    }

    /// Shared keys present for every kind (Android `SHARED_KEYS`). Order here is irrelevant —
    /// `controlKeyOrder` decides the layout; this list only defines membership.
    static let sharedTail: [TerminalControlKey] = [esc, ctrlC, enter, tab, backspace, up, down, left, right, ctrlO, ctrlR]
    static let claudeKeys: [TerminalControlKey] = ordered([mode, rewind] + sharedTail + [ctrlL])
    static let codexKeys: [TerminalControlKey] = ordered([approve, reject] + claudeKeys)
    static let antigravityKeys: [TerminalControlKey] = ordered([ctrlK, ctrlG, ctrlD] + sharedTail + [ctrlL])
    /// Kind-neutral minimal set for custom agent types and the not-yet-resolved
    /// state (Android `DEFAULT_CONTROL_KEYS`): nav + the universal
    /// interrupt/erase keys only. Ctrl+O/Ctrl+R/Ctrl+L are deliberately absent —
    /// they carry Claude-TUI meanings an arbitrary CLI may bind differently;
    /// the saved-shortcut "…" picker is the escape hatch.
    static let defaultKeys: [TerminalControlKey] = ordered([enter, up, down, left, right, ctrlC, esc, tab, backspace])

    /// Codex and Antigravity get their own sets (Android `controlKeysFor`).
    /// `nil` = kind not resolved yet: render the neutral set instead of
    /// guessing Claude (its Mode/Rewind escape codes are wrong input for
    /// other TUIs).
    static func keys(for kind: SessionKind?) -> [TerminalControlKey] {
        switch kind {
        case .codex: return codexKeys
        case .antigravity: return antigravityKeys
        case nil, .custom: return defaultKeys
        // Shell keeping the Claude set is a known oddity, deliberately
        // unchanged here.
        case .claude, .autoScan, .shell: return claudeKeys
        }
    }
}

/// Composer bar: a per-kind control-key row plus the message field. Submitting
/// builds the `m` frame; each control key sends its bytes as an `i` frame.
struct InputBar: View {
    @Bindable var model: TerminalSessionModel
    let onOpenTemplates: () -> Void
    let onSaveTemplate: () -> Void
    let onOpenShortcuts: () -> Void
    let onRenameSavedShortcut: (SavedShortcutUi) -> Void

    @FocusState private var inputFocused: Bool
    @Environment(\.mobaideColors) private var colors

    private var trimmedInput: String {
        model.inputText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var canSend: Bool {
        model.isConnected && model.isRunning && !trimmedInput.isEmpty
    }

    var body: some View {
        VStack(spacing: 6) {
            controlKeyRow
            // Fade only, never slide (Android parity): a slide animates the bar
            // height frame-by-frame, resizing the terminal — and the remote PTY —
            // through a burst of intermediate row counts. A fade changes height
            // in a single frame, so the toggle coalesces into one debounced resize.
            if model.composerVisible {
                composerRow
            } else {
                hiddenComposerRow
            }
        }
        .padding(.vertical, 6)
        .background(colors.surfaceVariant.opacity(0.3))
        .animation(.easeInOut(duration: 0.15), value: model.composerVisible)
        .task {
            for await _ in model.focusRequests() {
                // Let a just-revealed composer render before claiming focus —
                // setting @FocusState on a not-yet-mounted field is dropped.
                await Task.yield()
                inputFocused = true
            }
        }
    }

    private var controlKeyRow: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                // Dispatch on the resolved kind, not the construction seed:
                // a cold-start deep link seeds `.claude` before the true kind
                // is known, and the seed never updates (Android renders the
                // neutral set for an unresolved kind too).
                ForEach(TerminalControlKey.keys(for: model.resolvedKind)) { key in
                    Button {
                        model.sendControlKey(key.bytes)
                    } label: {
                        if let image = key.systemImage {
                            Image(systemName: image)
                        } else {
                            Text(key.label)
                        }
                    }
                    .buttonStyle(.bordered)
                    .font(.footnote)
                    // Same gate as every other byte-send surface (Android
                    // connected-AND-running parity): a key tapped during
                    // replay or after the session stopped must not feed the
                    // PTY.
                    .disabled(!model.canSendTerminalBytes)
                }
                // Paste without needing a selection first — an iOS-first
                // affordance; Android reaches paste only through its long-press
                // selection menu. Deliberately *not* also gated on
                // `Clipboard.hasText`: the pasteboard is not observable, so the
                // button would latch stale-disabled. An empty clipboard is a
                // silent no-op instead.
                Button {
                    model.pasteFromClipboard()
                } label: {
                    Image(systemName: "doc.on.clipboard")
                }
                .buttonStyle(.bordered)
                .font(.footnote)
                .disabled(!model.canSendTerminalBytes)
                .accessibilityLabel("Paste")
                Button(action: onOpenShortcuts) {
                    Image(systemName: "ellipsis")
                }
                .buttonStyle(.bordered)
                .font(.footnote)
                .disabled(!model.canSendTerminalBytes)
                .accessibilityLabel("Custom shortcut")
                ForEach(model.savedShortcutsForKind) { shortcut in
                    SavedShortcutKeyButton(
                        shortcut: shortcut,
                        enabled: model.canSendTerminalBytes,
                        onSend: { model.sendChord(shortcut.chord) },
                        onRename: { onRenameSavedShortcut(shortcut) },
                        onRemove: { model.removeShortcut(id: shortcut.id) }
                    )
                }
            }
            .padding(.horizontal, 4)
        }
    }

    private var composerRow: some View {
        HStack(spacing: 8) {
            Button {
                model.hideComposer()
            } label: {
                Image(systemName: "keyboard.chevron.compact.down")
            }
            .accessibilityLabel("Hide command field")

            Button(action: onOpenTemplates) {
                Image(systemName: "doc.text")
            }
            .disabled(!model.isConnected)

            TextField("Message", text: $model.inputText, axis: .vertical)
                .lineLimit(1...5)
                .textFieldStyle(.roundedBorder)
                .focused($inputFocused)

            Button(action: onSaveTemplate) {
                Image(systemName: "square.and.arrow.down")
            }
            .disabled(trimmedInput.isEmpty)

            Button(action: submit) {
                Image(systemName: "paperplane.fill")
            }
            .disabled(!canSend)
        }
        .padding(.horizontal, 8)
        .transition(.opacity)
    }

    /// Collapsed-composer row (Android `RevealComposerChip` + show-keyboard
    /// button). The keyboard button is a toggle with a state-reflecting icon —
    /// the deliberate iOS divergence: there is no system back gesture to
    /// dismiss the keyboard, so the same button must do both.
    private var hiddenComposerRow: some View {
        HStack(spacing: 8) {
            Button {
                model.toggleTerminalKeyboard()
            } label: {
                Image(systemName: model.terminalKeyboardActive ? "keyboard.chevron.compact.down" : "keyboard")
            }
            // Raising is pointless unless typed keys can actually reach the
            // PTY (the `sendTerminalBytes` gate), but dismissing must stay
            // available: a socket drop leaves the keyboard raised, and this
            // toggle is its only direct dismissal control.
            .disabled(!model.canSendTerminalBytes && !model.terminalKeyboardActive)
            .accessibilityLabel(model.terminalKeyboardActive ? "Hide keyboard" : "Show keyboard")

            Button {
                model.revealComposer()
            } label: {
                Label("Command field", systemImage: "keyboard")
            }
            .buttonStyle(.bordered)
            .font(.footnote)

            Spacer()
        }
        .padding(.horizontal, 8)
        .transition(.opacity)
    }

    private func submit() {
        var text = model.inputText
        while let last = text.last, last == "\n" || last == "\r" {
            text.removeLast()
        }
        guard !text.isEmpty else { return }
        model.sendInput(text)
    }
}

/// A saved custom shortcut rendered at the end of the control-key row
/// (Android `SavedShortcutKeyButton`). Never `.disabled`: a disabled control
/// would also swallow the context menu, and Rename/Remove must stay reachable
/// while the session gate is down. The tap guards itself instead, with the
/// dimmed label signalling inertness.
private struct SavedShortcutKeyButton: View {
    let shortcut: SavedShortcutUi
    let enabled: Bool
    let onSend: () -> Void
    let onRename: () -> Void
    let onRemove: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Button {
            guard enabled else { return }
            onSend()
        } label: {
            label
        }
        .buttonStyle(.bordered)
        .contextMenu {
            Button(action: onRename) {
                Label("Rename", systemImage: "pencil")
            }
            Button(role: .destructive, action: onRemove) {
                Label("Remove", systemImage: "trash")
            }
        }
        .accessibilityLabel("Saved shortcut \(shortcut.label)")
    }

    @ViewBuilder
    private var label: some View {
        let text = Text(shortcut.label)
            .font(.footnote.monospaced())
            .lineLimit(1)
        Group {
            if shortcut.risky {
                text.foregroundStyle(colors.tertiary)
            } else {
                text
            }
        }
        .opacity(enabled ? 1 : 0.38)
    }
}
