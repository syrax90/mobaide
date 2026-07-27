import SwiftUI

extension View {
    /// Rename dialog for a saved shortcut (Android `RenameShortcutDialog`),
    /// as an alert-with-textfield (the "Save as template" precedent). Attached
    /// both inside the picker sheet and on the terminal screen: an alert only
    /// appears above the presentation layer it was attached to, and each entry
    /// point (Saved-as hint vs control-row long-press) lives in a different one.
    func renameShortcutAlert(
        target: Binding<SavedShortcutUi?>,
        onRename: @escaping (SavedShortcutUi, String) -> Void
    ) -> some View {
        modifier(RenameShortcutAlertModifier(target: target, onRename: onRename))
    }
}

private struct RenameShortcutAlertModifier: ViewModifier {
    @Binding var target: SavedShortcutUi?
    let onRename: (SavedShortcutUi, String) -> Void

    @State private var name = ""

    func body(content: Content) -> some View {
        content
            .alert(
                "Rename shortcut",
                isPresented: Binding(
                    get: { target != nil },
                    set: { if !$0 { target = nil } }
                ),
                presenting: target
            ) { shortcut in
                TextField("Button name", text: $name)
                Button("Rename") {
                    onRename(shortcut, name)
                }
                Button("Cancel", role: .cancel) {}
            } message: { shortcut in
                Text("\(shortcut.chord.display) — leave empty to show the shortcut itself")
            }
            .onChange(of: target) { _, newTarget in
                if let newTarget {
                    name = newTarget.name ?? ""
                }
            }
            .onChange(of: name) { _, newValue in
                // The server caps names at 100; clamp while typing (Android
                // parity) instead of failing the rename after the fact.
                let clamped = ShortcutPickerLogic.clampName(newValue)
                if clamped != newValue {
                    name = clamped
                }
            }
    }
}
