import PhotosUI
import SwiftUI

/// The chat composer: slash-command suggestions, picked images, the message
/// field, then a row of attach / templates / save as template and mic /
/// expand / Send (Stop while a turn runs and the field is empty), as on
/// Android. Return inserts a newline; only the button sends.
struct ChatComposer: View {
    @Bindable var model: ChatSessionModel
    let voice: VoiceInputModel
    let onOpenTemplates: () -> Void
    let onSaveTemplate: () -> Void
    let onExpand: () -> Void

    @Environment(\.mobaideColors) private var colors
    @FocusState private var focused: Bool
    @State private var pickerItems: [PhotosPickerItem] = []

    private static let maxSuggestions = 6

    var body: some View {
        VStack(spacing: 8) {
            if !suggestions.isEmpty {
                suggestionList
            }
            if !model.attachments.isEmpty || model.preparingImages > 0 {
                attachmentRow
            }
            if showsDictationBar {
                VoiceDictationBar(voice: voice)
                    .transition(.opacity)
            }
            field
            HStack(spacing: 4) {
                if model.state.promptImages {
                    attachButton
                }
                Button(action: onOpenTemplates) {
                    Image(systemName: "doc.text")
                        .frame(minWidth: 40, minHeight: 44)
                        .contentShape(Rectangle())
                }
                .disabled(model.sessionClosed)
                .accessibilityLabel("Insert template")
                .accessibilityIdentifier("chat-templates-button")
                Button(action: onSaveTemplate) {
                    Image(systemName: "square.and.arrow.down")
                        .frame(minWidth: 40, minHeight: 44)
                        .contentShape(Rectangle())
                }
                .disabled(model.composerText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityLabel("Save as template")
                .accessibilityIdentifier("chat-save-template-button")
                Spacer(minLength: 0)
                if voice.micMode != .hidden {
                    VoiceMicButton(voice: voice)
                        .frame(minWidth: 40, minHeight: 44)
                }
                // Same slot as the terminal's: right before Send.
                Button(action: onExpand) {
                    Image(systemName: "arrow.up.left.and.arrow.down.right")
                        .frame(minWidth: 40, minHeight: 44)
                        .contentShape(Rectangle())
                }
                .disabled(model.sessionClosed)
                .accessibilityLabel("Expand editor")
                .accessibilityIdentifier("chat-expand-composer-button")
                sendButton
                    .padding(.leading, 4)
            }
        }
        .padding(EdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 12))
        .onChange(of: pickerItems) { _, items in
            guard !items.isEmpty else { return }
            pickerItems = []
            for item in items {
                model.addImage { try? await item.loadTransferable(type: Data.self) }
            }
        }
    }

    // MARK: - Field

    private var field: some View {
        ZStack(alignment: .topLeading) {
            ComposerTextField(
                text: $model.composerText,
                selection: $model.composerSelection,
                cursorRequest: model.composerCursorRequest,
                style: .inline,
                focus: $focused
            )
            .lineLimit(1...6)
            if let hint = commandHint {
                Text("\(Text(model.composerText).foregroundStyle(.clear))\(Text(hint).foregroundStyle(colors.gray))")
                    .lineLimit(1)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
        }
        .pixelField(isFocused: focused)
        .disabled(model.sessionClosed)
        .accessibilityLabel(ComposerTextField.defaultPlaceholder)
    }

    /// The picked command's argument hint, shown after `/name ` until typing starts.
    private var commandHint: String? {
        let text = model.composerText
        guard text.hasPrefix("/"), text.hasSuffix(" "), text.filter({ $0 == " " }).count == 1 else { return nil }
        let name = String(text.dropFirst().dropLast())
        guard let hint = model.state.commands.first(where: { $0.name == name })?.hint, !hint.isEmpty else { return nil }
        return hint
    }

    // MARK: - Slash commands

    private var suggestions: [ChatSlashCommand] {
        let text = model.composerText
        guard text.hasPrefix("/"), !text.contains(where: \.isWhitespace) else { return [] }
        let query = text.dropFirst().lowercased()
        let commands = model.state.commands
        let prefix = commands.filter { $0.name.lowercased().hasPrefix(query) }
        let rest = query.isEmpty ? [] : commands.filter {
            !$0.name.lowercased().hasPrefix(query) && $0.name.lowercased().contains(query)
        }
        return prefix + rest
    }

    private var suggestionList: some View {
        let list = suggestions
        return ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(list) { command in
                    Button {
                        model.applySlashCommand(command)
                    } label: {
                        HStack(spacing: 10) {
                            Text("/\(command.name)")
                                .font(.callout.monospaced())
                                .foregroundStyle(colors.accent)
                            if let description = command.description {
                                Text(description)
                                    .font(.caption)
                                    .foregroundStyle(colors.textMuted)
                                    .lineLimit(1)
                            }
                            Spacer(minLength: 0)
                        }
                        .padding(.horizontal, 12)
                        .frame(minHeight: 40)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("/\(command.name), \(command.description ?? "")")
                }
            }
        }
        .frame(height: CGFloat(min(list.count, Self.maxSuggestions)) * 40)
        .background(colors.bg2)
        .pixelRing(colors.surface2)
    }

    // MARK: - Attachments

    private var attachButton: some View {
        PhotosPicker(
            selection: $pickerItems,
            maxSelectionCount: max(1, ChatSessionModel.maxImages - model.attachments.count - model.preparingImages),
            matching: .images
        ) {
            Image(systemName: "photo.badge.plus")
                .frame(minWidth: 36, minHeight: 44)
                .contentShape(Rectangle())
        }
        .disabled(!model.canAttachImages || model.sessionClosed)
        .accessibilityLabel("Attach image")
    }

    private var attachmentRow: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 10) {
                ForEach(model.attachments) { image in
                    Image(uiImage: image.thumbnail)
                        .resizable()
                        .scaledToFill()
                        .frame(width: 56, height: 56)
                        .clipped()
                        .pixelRing(colors.surface2)
                        .overlay(alignment: .topTrailing) {
                            Button {
                                model.removeImage(image.id)
                            } label: {
                                Image(systemName: "xmark")
                                    .font(.caption2.weight(.bold))
                                    .foregroundStyle(.white)
                                    .frame(width: 20, height: 20)
                                    .background(Color.black.opacity(0.7))
                                    .frame(width: 44, height: 44, alignment: .topTrailing)
                                    .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .offset(x: 6, y: -6)
                            .accessibilityLabel("Remove image")
                        }
                }
                ForEach(0..<model.preparingImages, id: \.self) { _ in
                    PixelLoadingBlock(width: 8, height: 14)
                        .frame(width: 56, height: 56)
                        .background(colors.surface)
                        .pixelRing(colors.surface2)
                }
            }
            .padding(.top, 6)
            .padding(.trailing, 8)
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    // MARK: - Buttons

    private var showsDictationBar: Bool {
        switch voice.dictation {
        case .idle, .result: return false
        default: return true
        }
    }

    @ViewBuilder
    private var sendButton: some View {
        if model.showsStop {
            Button {
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                model.stop()
            } label: {
                Image(systemName: "stop.fill")
                    .font(.system(size: 15, weight: .bold))
                    .frame(width: 22, height: 28)
            }
            .buttonStyle(PixelButtonStyle(variant: .danger, size: .compact))
            .disabled(!model.isLive || model.state.cancelling)
            .keyboardShortcut(.escape, modifiers: [])
            .accessibilityLabel("Stop")
        } else {
            Button {
                model.send()
            } label: {
                Image(systemName: "arrow.up")
                    .font(.system(size: 15, weight: .bold))
                    .frame(width: 22, height: 28)
            }
            .buttonStyle(PixelButtonStyle(variant: .primary, size: .compact))
            .disabled(!model.canSend)
            .accessibilityLabel(model.isRunning ? "Queue message" : "Send")
        }
    }
}

/// The chat composer over the whole screen, for long messages (the terminal's
/// `FullScreenInputSheet`). It edits the same draft as the inline field;
/// Return inserts a newline and only Send sends.
struct ChatFullScreenComposer: View {
    @Bindable var model: ChatSessionModel
    var voice: VoiceInputModel?
    var onSent: () -> Void = {}

    @Environment(\.dismiss) private var dismiss
    @Environment(\.mobaideColors) private var colors
    @FocusState private var editorFocused: Bool

    var body: some View {
        NavigationStack {
            editor
                .navigationTitle("Compose message")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        // Not "Cancel": collapsing keeps the draft.
                        Button {
                            dismiss()
                        } label: {
                            Image(systemName: "arrow.down.right.and.arrow.up.left")
                        }
                        .accessibilityLabel("Collapse editor")
                    }
                    ToolbarItemGroup(placement: .confirmationAction) {
                        if let voice, voice.micMode == .ready {
                            VoiceMicButton(voice: voice)
                        }
                        Button {
                            model.send()
                            onSent()
                            dismiss()
                        } label: {
                            Image(systemName: "paperplane.fill")
                        }
                        .disabled(!model.canSend)
                        .accessibilityLabel(model.isRunning ? "Queue message" : "Send")
                    }
                }
                .safeAreaInset(edge: .bottom) {
                    if let voice, voice.dictation != .idle, !voice.dictation.isResult {
                        VoiceDictationBar(voice: voice)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                    }
                }
        }
        .pixelSheet()
        .task {
            // The field must be mounted before `@FocusState` takes.
            await Task.yield()
            editorFocused = true
        }
    }

    private var editor: some View {
        ComposerTextField(
            text: $model.composerText,
            selection: $model.composerSelection,
            cursorRequest: model.composerCursorRequest,
            style: .editor,
            focus: $editorFocused
        )
        .foregroundStyle(colors.text)
        .scrollContentBackground(.hidden)
        .disabled(model.sessionClosed)
        .padding(.horizontal, 12)
        .background(colors.bg)
        .overlay(alignment: .topLeading) {
            // `TextEditor` has no placeholder slot.
            if model.composerText.isEmpty {
                Text("Write your message…")
                    .foregroundStyle(colors.gray)
                    .padding(.horizontal, 17)
                    .padding(.top, 8)
                    .allowsHitTesting(false)
            }
        }
    }
}
