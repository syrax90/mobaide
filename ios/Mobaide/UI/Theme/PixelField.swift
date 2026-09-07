import SwiftUI

extension View {
    /// Web `.field-input`: `surface` plate, 2pt `gray` ring; focus → `accent`,
    /// invalid → `danger`. A modifier rather than a `TextFieldStyle` because the
    /// style cannot observe focus.
    func pixelField(isInvalid: Bool = false, isFocused: Bool = false) -> some View {
        modifier(PixelFieldModifier(isInvalid: isInvalid, isFocused: isFocused))
    }
}

private struct PixelFieldModifier: ViewModifier {
    let isInvalid: Bool
    let isFocused: Bool

    @Environment(\.mobaideColors) private var colors

    func body(content: Content) -> some View {
        content
            .foregroundStyle(colors.text)
            .padding(PixelMetrics.fieldInsets)
            .background(colors.surface)
            .pixelRing(isInvalid ? colors.danger : isFocused ? colors.accent : colors.gray)
    }
}

/// Web `.field`: chrome label above the control, hint or error below.
struct PixelLabeledField<Field: View>: View {
    let label: String
    var hint: String? = nil
    var error: String? = nil
    @ViewBuilder let field: () -> Field

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .sectionHeading()
            field()
            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(colors.danger)
            } else if let hint {
                Text(hint)
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
            }
        }
    }
}
