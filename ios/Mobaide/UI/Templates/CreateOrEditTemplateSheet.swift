import SwiftUI

/// Create/edit a prompt template (Android `CreateOrEditTemplateDialog`).
/// Save is enabled only when the trimmed name and the raw body are both
/// non-empty; the name is trimmed on save, the body is sent verbatim.
struct CreateOrEditTemplateSheet: View {
    let target: TemplateDto?
    let onSave: (String, String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var bodyText: String

    init(target: TemplateDto?, onSave: @escaping (String, String) -> Void) {
        self.target = target
        self.onSave = onSave
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
                    TextEditor(text: $bodyText)
                        .font(MobaideTypography.code)
                        .frame(minHeight: 160)
                }
            }
            .navigationTitle(target == nil ? "New template" : "Edit template")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        onSave(name.trimmingCharacters(in: .whitespacesAndNewlines), bodyText)
                        dismiss()
                    }
                    .disabled(!canSave)
                }
            }
        }
    }
}
