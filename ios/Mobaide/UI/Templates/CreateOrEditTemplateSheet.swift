import SwiftUI

/// Create/edit a prompt template (Android `CreateOrEditTemplateDialog`).
/// Save is enabled only when the trimmed name and the raw body are both
/// non-empty; the name is trimmed on save, the body is sent verbatim. The
/// sheet observes `TemplatesModel.isSavingTemplate` so that, while a save is in
/// flight, Save/Cancel and interactive dismissal are blocked — the model
/// clears `editorTarget` (dismissing) only after the save succeeds, keeping the
/// draft intact on failure.
struct CreateOrEditTemplateSheet: View {
    let model: TemplatesModel
    let target: TemplateDto?

    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var bodyText: String

    init(model: TemplatesModel, target: TemplateDto?) {
        self.model = model
        self.target = target
        _name = State(initialValue: target?.name ?? "")
        _bodyText = State(initialValue: target?.body ?? "")
    }

    private var canSave: Bool {
        Self.isSaveEnabled(name: name, body: bodyText)
    }

    /// Save rule (Android parity): the name must be non-empty after trimming;
    /// the body must be non-empty but is checked and sent verbatim (not trimmed).
    static func isSaveEnabled(name: String, body: String) -> Bool {
        !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !body.isEmpty
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Name") {
                    TextField("Name", text: $name)
                }
                Section("Prompt body") {
                    // Same verbatim-input rule as the terminal composer: a saved
                    // body is sent to the PTY unchanged, so `ls -la` must not be
                    // stored as `Ls -la`.
                    TextEditor(text: $bodyText)
                        .font(MobaideTypography.code)
                        .frame(minHeight: 160)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                }
            }
            .navigationTitle(target == nil ? "New template" : "Edit template")
            .navigationBarTitleDisplayMode(.inline)
            .interactiveDismissDisabled(model.isSavingTemplate)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    // Disabled mid-save so a Cancel can't dismiss the sheet while
                    // the create/update still commits.
                    Button("Cancel") { dismiss() }
                        .disabled(model.isSavingTemplate)
                }
                ToolbarItem(placement: .confirmationAction) {
                    // No `dismiss()` — the model clears `editorTarget` only after
                    // a successful save, so a failed save keeps the draft intact.
                    Button("Save") {
                        model.saveFromEditor(
                            name: name.trimmingCharacters(in: .whitespacesAndNewlines),
                            body: bodyText
                        )
                    }
                    .disabled(!canSave || model.isSavingTemplate)
                }
            }
        }
    }
}
