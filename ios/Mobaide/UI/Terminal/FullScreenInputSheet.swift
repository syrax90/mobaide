import SwiftUI

/// Expanded prompt editor (Android `FullScreenInputSheet`). Return inserts a
/// newline — there is no `onSubmit`/`submitLabel` and the only submit path is
/// the toolbar's Send button. That is the whole point of the screen: long
/// multi-line prompts cannot be composed in a field whose Return key sends.
struct FullScreenInputSheet: View {
    @Bindable var model: TerminalSessionModel

    @Environment(\.dismiss) private var dismiss
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
                    ToolbarItem(placement: .confirmationAction) {
                        Button {
                            model.submitComposer()
                            dismiss()
                        } label: {
                            Image(systemName: "paperplane.fill")
                        }
                        .disabled(!model.canSubmitComposer)
                        .accessibilityLabel("Send")
                    }
                }
        }
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
        TextEditor(text: $model.inputText)
            .font(MobaideTypography.code)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .focused($editorFocused)
            .disabled(!model.isComposerActive)
            .padding(.horizontal, 12)
            .overlay(alignment: .topLeading) {
                // `TextEditor` has no placeholder slot.
                if model.inputText.isEmpty {
                    Text("Compose your prompt…")
                        .font(MobaideTypography.code)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 17)
                        .padding(.top, 8)
                        .allowsHitTesting(false)
                }
            }
    }
}
