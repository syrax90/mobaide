import SwiftUI

/// Create/edit an account-scoped custom agent type (Android
/// `CustomAgentTypeEditorSheet`). While a save is in flight, Save/Cancel and
/// interactive dismissal are blocked; the model clears `typeEditorTarget`
/// (dismissing) only after the save succeeds, keeping the draft intact on
/// failure. Server 409s (20-type cap, name conflict) surface inline.
struct CustomAgentTypeEditorSheet: View {
    let model: CreateSessionModel
    let target: CustomAgentTypeDto?

    @Environment(\.dismiss) private var dismiss
    @Environment(\.mobaideColors) private var colors
    @State private var name: String
    @State private var command: String

    init(model: CreateSessionModel, target: CustomAgentTypeDto?) {
        self.model = model
        self.target = target
        _name = State(initialValue: target?.name ?? "")
        _command = State(initialValue: target?.command ?? "")
    }

    private var canSave: Bool {
        Self.isSaveEnabled(name: name, command: command)
    }

    /// Save-button rule (Android parity): both fields non-blank after
    /// trimming; the length limits surface inline on save instead.
    static func isSaveEnabled(name: String, command: String) -> Bool {
        !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Name") {
                    TextField("Name", text: $name)
                }
                Section("Launch command") {
                    TextField("e.g. aider --model sonnet", text: $command, axis: .vertical)
                        .font(MobaideTypography.code)
                        .lineLimit(3...8)
                        .autocorrectionDisabled(true)
                        .textInputAutocapitalization(.never)
                        .onChange(of: command) { _, newValue in
                            // Live smart-punctuation undo, covering both typing
                            // and paste (keyboard traits don't affect paste);
                            // the model sanitizes again on save. Guarded so the
                            // rewrite cannot re-trigger itself.
                            let sanitized = CreateSessionModel.sanitizeCommandPunctuation(newValue)
                            if sanitized != newValue { command = sanitized }
                        }
                    Text("Runs in your login shell inside the session directory")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                if let error = model.typeEditorError {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(colors.error)
                }
            }
            .navigationTitle(target == nil ? "New custom agent" : "Edit custom agent")
            .navigationBarTitleDisplayMode(.inline)
            .interactiveDismissDisabled(model.isSavingType)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    // Disabled mid-save so a Cancel can't dismiss the sheet
                    // while the create/update still commits.
                    Button("Cancel") { dismiss() }
                        .disabled(model.isSavingType)
                }
                ToolbarItem(placement: .confirmationAction) {
                    // No `dismiss()` — the model clears `typeEditorTarget` only
                    // after a successful save, so a failed save keeps the draft.
                    Button(model.isSavingType ? "Saving…" : "Save") {
                        Task { await model.saveCustomType(name: name, command: command) }
                    }
                    .disabled(!canSave || model.isSavingType)
                }
            }
        }
    }
}
