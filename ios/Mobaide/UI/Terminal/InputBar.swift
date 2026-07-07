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

    /// Shared tail present for every kind (Android `SHARED_TAIL`).
    static let sharedTail: [TerminalControlKey] = [esc, ctrlC, enter, tab, up, down, left, right, ctrlO, ctrlR]
    static let claudeKeys: [TerminalControlKey] = [mode, rewind] + sharedTail + [ctrlL]
    static let codexKeys: [TerminalControlKey] = [approve, reject] + claudeKeys
    static let antigravityKeys: [TerminalControlKey] = [ctrlK, ctrlG, ctrlD] + sharedTail + [ctrlL]

    /// Codex and Antigravity get their own sets; Claude, Shell and AutoScan
    /// all use the Claude set (Android `controlKeysFor`).
    static func keys(for kind: SessionKind) -> [TerminalControlKey] {
        switch kind {
        case .codex: return codexKeys
        case .antigravity: return antigravityKeys
        default: return claudeKeys
        }
    }
}

/// Composer bar: a per-kind control-key row plus the message field. Submitting
/// builds the `m` frame; each control key sends its bytes as an `i` frame.
struct InputBar: View {
    @Bindable var model: TerminalSessionModel
    let onOpenTemplates: () -> Void
    let onSaveTemplate: () -> Void

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
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(TerminalControlKey.keys(for: model.kind)) { key in
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
                        .disabled(!model.isConnected)
                    }
                }
                .padding(.horizontal, 4)
            }

            HStack(spacing: 8) {
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
        }
        .padding(.vertical, 6)
        .background(colors.surfaceVariant.opacity(0.3))
        .task {
            for await _ in model.focusRequests() {
                inputFocused = true
            }
        }
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
