import SwiftUI

/// One transient bottom message (Android Snackbar stand-in). The fresh `id`
/// makes consecutive identical texts distinct, so re-emitting the same error
/// still re-triggers the toast (SharedFlow-channel parity).
struct ToastMessage: Equatable {
    let id: UUID
    let text: String
    /// Web `.toast-success` / `.toast-error`: recolours text and ring. `nil`
    /// is the informational default.
    let tone: PixelTone?

    init(text: String, tone: PixelTone? = nil) {
        self.id = UUID()
        self.text = text
        self.tone = tone
    }
}

extension View {
    func toast(_ message: Binding<ToastMessage?>) -> some View {
        modifier(ToastModifier(message: message))
    }
}

/// Web `.toast`: a `surface` plate on a hard black shadow that pops in with no
/// easing (the web's `steps(2)` entry is two frames long).
private struct ToastModifier: ViewModifier {
    @Binding var message: ToastMessage?

    @Environment(\.mobaideColors) private var colors

    func body(content: Content) -> some View {
        content
            .overlay(alignment: .bottom) {
                if let message {
                    Text(message.text)
                        .font(.footnote)
                        .foregroundStyle(message.tone.map(colors.color) ?? colors.text)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(EdgeInsets(top: 10, leading: 14, bottom: 10, trailing: 14))
                        .background(colors.surface)
                        .pixelRing(message.tone.map(colors.ring) ?? colors.rule)
                        .pixelShadow(MobaideColorScheme.modalShadow)
                        .padding(.horizontal, 24)
                        .padding(.bottom, 16)
                        .accessibilityAddTraits(.updatesFrequently)
                }
            }
            .transaction { $0.animation = nil }
            .task(id: message?.id) {
                guard message != nil else { return }
                try? await Task.sleep(for: .seconds(3))
                if !Task.isCancelled {
                    message = nil
                }
            }
    }
}
