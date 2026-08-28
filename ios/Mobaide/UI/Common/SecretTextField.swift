import SwiftUI

/// Mirror of Android `SecretTextField`: single-line secret entry with a
/// reveal toggle. Stateless — value and reveal state are hoisted.
struct SecretTextField: View {
    @Binding var text: String
    let label: String
    @Binding var revealed: Bool
    var placeholder: String = ""
    var enabled: Bool = true
    var isInvalid: Bool = false
    /// Drives the accent ring. Supplied by the caller from its own
    /// `@FocusState` — a second focus binding inside the component would
    /// compete with the caller's `.focused(_:equals:)` for the one focusable.
    var isFocused: Bool = false

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack {
            Group {
                if revealed {
                    TextField(placeholder.isEmpty ? label : placeholder, text: $text)
                } else {
                    SecureField(placeholder.isEmpty ? label : placeholder, text: $text)
                }
            }
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            Button {
                revealed.toggle()
            } label: {
                Image(systemName: revealed ? "eye.slash" : "eye")
                    .foregroundStyle(colors.textMuted)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(revealed ? "Hide \(label)" : "Show \(label)")
        }
        .pixelField(isInvalid: isInvalid, isFocused: isFocused)
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.5)
        .accessibilityIdentifier("secret-field")
    }
}
