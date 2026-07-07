import SwiftUI

/// One transient bottom message (Android Snackbar stand-in). The fresh `id`
/// makes consecutive identical texts distinct, so re-emitting the same error
/// still re-triggers the toast (SharedFlow-channel parity).
struct ToastMessage: Equatable {
    let id: UUID
    let text: String

    init(text: String) {
        self.id = UUID()
        self.text = text
    }
}

extension View {
    func toast(_ message: Binding<ToastMessage?>) -> some View {
        modifier(ToastModifier(message: message))
    }
}

private struct ToastModifier: ViewModifier {
    @Binding var message: ToastMessage?

    @Environment(\.mobaideColors) private var colors

    func body(content: Content) -> some View {
        content
            .overlay(alignment: .bottom) {
                if let message {
                    Text(message.text)
                        .font(.footnote)
                        .foregroundStyle(colors.onSurfaceVariant)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .background(colors.surfaceVariant, in: RoundedRectangle(cornerRadius: 8))
                        .padding(.horizontal, 24)
                        .padding(.bottom, 16)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                        .accessibilityAddTraits(.updatesFrequently)
                }
            }
            .animation(.easeInOut(duration: 0.2), value: message)
            .task(id: message?.id) {
                guard message != nil else { return }
                try? await Task.sleep(for: .seconds(3))
                if !Task.isCancelled {
                    message = nil
                }
            }
    }
}
