import SwiftUI

/// A transient bottom snackbar with a single action (Android Snackbar + action
/// parity). Unlike `ToastOverlay`, it carries an action button and does **not**
/// auto-dismiss itself: the owning `@Observable` model drives the 5-second
/// window and any delayed commit, so a view-identity change can never silently
/// cancel the pending work. Set the binding to nil to hide it.
struct UndoToast: Equatable {
    let id: UUID
    let message: String

    init(message: String) {
        self.id = UUID()
        self.message = message
    }
}

extension View {
    func undoToast(
        _ toast: Binding<UndoToast?>,
        actionLabel: String = "Undo",
        onAction: @escaping () -> Void
    ) -> some View {
        modifier(UndoToastModifier(toast: toast, actionLabel: actionLabel, onAction: onAction))
    }
}

private struct UndoToastModifier: ViewModifier {
    @Binding var toast: UndoToast?
    let actionLabel: String
    let onAction: () -> Void

    @Environment(\.mobaideColors) private var colors

    func body(content: Content) -> some View {
        content
            .overlay(alignment: .bottom) {
                if let toast {
                    HStack(spacing: 12) {
                        Text(toast.message)
                            .font(.footnote)
                            .foregroundStyle(colors.onSurfaceVariant)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Button(actionLabel, action: onAction)
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(colors.primary)
                            .buttonStyle(.plain)
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .background(colors.surfaceVariant, in: RoundedRectangle(cornerRadius: 8))
                    .padding(.horizontal, 24)
                    .padding(.bottom, 16)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .accessibilityAddTraits(.updatesFrequently)
                }
            }
            .animation(.easeInOut(duration: 0.2), value: toast)
    }
}
