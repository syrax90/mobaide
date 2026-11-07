import SwiftUI

/// Web `.button` / `.button-primary` / `.button-danger`: a square box on a
/// hard 4pt shadow; pressing moves the box into its shadow with no easing.
struct PixelButtonStyle: ButtonStyle {
    enum Variant {
        /// `dangerGhost`: the ghost button in the danger colour, for a
        /// destructive choice that is not the primary action.
        case ghost, primary, danger, dangerGhost
    }

    enum Size {
        /// `compact`: the small box with the body face, for an icon button
        /// whose label must stay mixed case for VoiceOver.
        case regular, small, compact
    }

    var variant: Variant = .ghost
    var size: Size = .regular
    var fullWidth = false

    @Environment(\.mobaideColors) private var colors
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        let pressed = configuration.isPressed && isEnabled
        return label(configuration.label)
            .foregroundStyle(foreground)
            .frame(maxWidth: fullWidth ? .infinity : nil)
            .frame(minHeight: size == .regular ? 36 : 28)
            .padding(size == .regular ? PixelMetrics.buttonInsets : PixelMetrics.smallButtonInsets)
            .background(fill)
            .pixelRing(ring)
            .offset(x: pressed ? PixelMetrics.px : 0, y: pressed ? PixelMetrics.px : 0)
            .pixelShadow(shadow, visible: isEnabled && !pressed)
            .contentShape(Rectangle())
            .transaction { $0.animation = nil }
    }

    @ViewBuilder
    private func label(_ label: Configuration.Label) -> some View {
        switch size {
        case .regular, .compact:
            label.font(MobaideTypography.bodyLarge)
        case .small:
            label.chromeText()
        }
    }

    private var foreground: Color {
        guard isEnabled else { return colors.gray }
        switch variant {
        case .ghost: return colors.accent
        case .dangerGhost: return colors.danger
        case .primary, .danger: return .white
        }
    }

    private var fill: Color {
        guard isEnabled else { return colors.surface2 }
        switch variant {
        case .ghost, .dangerGhost: return .clear
        case .primary: return MobaideColorScheme.brand
        case .danger: return MobaideColorScheme.dangerFill
        }
    }

    private var ring: Color {
        guard isEnabled else { return colors.rule }
        switch variant {
        case .ghost: return MobaideColorScheme.brand2
        case .dangerGhost: return colors.danger
        case .primary, .danger: return .clear
        }
    }

    private var shadow: Color {
        switch variant {
        case .ghost, .primary: return MobaideColorScheme.btnShadow
        case .danger, .dangerGhost: return MobaideColorScheme.dangerFillShadow
        }
    }
}

extension ButtonStyle where Self == PixelButtonStyle {
    static var pixel: PixelButtonStyle { PixelButtonStyle() }
    static var pixelPrimary: PixelButtonStyle { PixelButtonStyle(variant: .primary) }
    static var pixelDanger: PixelButtonStyle { PixelButtonStyle(variant: .danger) }
    static var pixelSmall: PixelButtonStyle { PixelButtonStyle(size: .small) }
    static var pixelSmallPrimary: PixelButtonStyle { PixelButtonStyle(variant: .primary, size: .small) }
    static var pixelSmallDanger: PixelButtonStyle { PixelButtonStyle(variant: .danger, size: .small) }
    static var pixelBlock: PixelButtonStyle { PixelButtonStyle(fullWidth: true) }
    static var pixelPrimaryBlock: PixelButtonStyle { PixelButtonStyle(variant: .primary, fullWidth: true) }
    static var pixelDangerBlock: PixelButtonStyle { PixelButtonStyle(variant: .danger, fullWidth: true) }
    static var pixelDangerGhostBlock: PixelButtonStyle { PixelButtonStyle(variant: .dangerGhost, fullWidth: true) }
}
