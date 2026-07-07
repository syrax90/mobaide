import SwiftUI

/// Mirror of Android `SecretTextField`: single-line secret entry with a
/// reveal toggle. Stateless — value and reveal state are hoisted.
struct SecretTextField: View {
    @Binding var text: String
    let label: String
    @Binding var revealed: Bool
    var placeholder: String = ""
    var enabled: Bool = true

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
                    .foregroundStyle(colors.onSurfaceVariant)
            }
            .accessibilityLabel(revealed ? "Hide \(label)" : "Show \(label)")
        }
        .padding(12)
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(colors.outline, lineWidth: 1)
        )
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.5)
        .accessibilityIdentifier("secret-field")
    }
}
