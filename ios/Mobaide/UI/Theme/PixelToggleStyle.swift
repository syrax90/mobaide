import SwiftUI

/// Web `input[type=checkbox]`: an 18pt square, `brand` fill when on. Installed
/// app-wide by `mobaideTheme()`.
struct PixelCheckboxToggleStyle: ToggleStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        Button {
            configuration.isOn.toggle()
        } label: {
            HStack(spacing: 12) {
                configuration.label
                Spacer(minLength: 12)
                PixelCheckbox(isOn: configuration.isOn)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .opacity(isEnabled ? 1 : 0.55)
        .transaction { $0.animation = nil }
        .accessibilityRepresentation {
            Toggle(isOn: configuration.$isOn) { configuration.label }
        }
    }
}

extension ToggleStyle where Self == PixelCheckboxToggleStyle {
    static var pixelCheckbox: PixelCheckboxToggleStyle { PixelCheckboxToggleStyle() }
}

struct PixelCheckbox: View {
    let isOn: Bool

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Rectangle()
            .fill(isOn ? MobaideColorScheme.brand : colors.surface)
            .frame(width: PixelMetrics.checkbox, height: PixelMetrics.checkbox)
            .pixelRing(isOn ? MobaideColorScheme.brand2 : colors.gray)
    }
}

/// Web `input[type=radio]`: still a square, told apart from the checkbox by
/// its cored centre (2pt `brand2` ring, `surface` gap, 6pt `brand` core).
struct PixelRadio: View {
    let isSelected: Bool

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Rectangle()
            .fill(isSelected ? MobaideColorScheme.brand : colors.surface)
            .frame(width: PixelMetrics.radio, height: PixelMetrics.radio)
            .overlay {
                if isSelected {
                    Rectangle()
                        .strokeBorder(colors.surface, lineWidth: 3)
                        .padding(PixelMetrics.ring)
                }
            }
            .pixelRing(isSelected ? MobaideColorScheme.brand2 : colors.gray)
    }
}
