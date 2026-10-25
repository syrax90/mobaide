import SwiftUI

/// Expanded prompt editor (Android `FullScreenInputSheet`). Return inserts a
/// newline — there is no `onSubmit`/`submitLabel` and the only submit path is
/// the toolbar's Send button. That is the whole point of the screen: long
/// multi-line prompts cannot be composed in a field whose Return key sends.
struct FullScreenInputSheet: View {
    @Bindable var model: TerminalSessionModel
    var voice: VoiceInputModel?
    var onSent: () -> Void = {}

    @Environment(\.dismiss) private var dismiss
    @Environment(\.mobaideColors) private var colors
    @FocusState private var editorFocused: Bool

    var body: some View {
        NavigationStack {
            editor
                .navigationTitle("Compose prompt")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        // Not "Cancel": collapsing keeps the draft, it does not
                        // discard it.
                        Button {
                            dismiss()
                        } label: {
                            Image(systemName: "arrow.down.right.and.arrow.up.left")
                        }
                        .accessibilityLabel("Collapse editor")
                    }
                    ToolbarItemGroup(placement: .confirmationAction) {
                        // Dictate only: the setup sheet hangs off the terminal,
                        // which cannot present it over this editor.
                        if let voice, voice.micMode == .ready {
                            VoiceMicButton(voice: voice)
                        }
                        Button {
                            model.submitComposer()
                            onSent()
                            dismiss()
                        } label: {
                            Image(systemName: "paperplane.fill")
                        }
                        .disabled(!model.canSubmitComposer)
                        .accessibilityLabel("Send")
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
            // Same yield-before-focus ordering as the inline composer: the
            // field must be mounted before `@FocusState` takes, or the request
            // is dropped.
            await Task.yield()
            editorFocused = true
        }
    }

    private var editor: some View {
        // Same verbatim-input rule as the composer this expands: the text goes
        // straight to the PTY, so the keyboard must not capitalize or "correct"
        // command tokens.
        ComposerTextField(model: model, style: .editor, focus: $editorFocused)
            .font(MobaideTypography.code)
            .foregroundStyle(colors.text)
            .scrollContentBackground(.hidden)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .disabled(!model.isComposerActive)
            .padding(.horizontal, 12)
            .background(colors.bg)
            .overlay(alignment: .topLeading) {
                // `TextEditor` has no placeholder slot.
                if model.inputText.isEmpty {
                    Text("Compose your prompt…")
                        .font(MobaideTypography.code)
                        .foregroundStyle(colors.gray)
                        .padding(.horizontal, 17)
                        .padding(.top, 8)
                        .allowsHitTesting(false)
                }
            }
    }
}
