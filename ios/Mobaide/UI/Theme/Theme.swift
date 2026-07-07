import SwiftUI

/// Mirror of Android `MobaideTheme` with **static** seed schemes only —
/// iOS has no Material-You dynamic color, so the fallback schemes from
/// `Theme.kt` become the one and only palette. Role values beyond the
/// three seeds are the Material 3 baseline tokens that Android's
/// `darkColorScheme()`/`lightColorScheme()` defaults produce.
struct MobaideColorScheme: Equatable, Sendable {
    let primary: Color
    let secondary: Color
    let tertiary: Color
    let error: Color
    let errorContainer: Color
    let onErrorContainer: Color
    let surfaceVariant: Color
    let onSurfaceVariant: Color
    let outline: Color
    let outlineVariant: Color

    static let dark = MobaideColorScheme(
        primary: MobaideColors.purple80,
        secondary: MobaideColors.purpleGrey80,
        tertiary: MobaideColors.pink80,
        error: Color(hex: 0xF2B8B5),
        errorContainer: Color(hex: 0x8C1D18),
        onErrorContainer: Color(hex: 0xF9DEDC),
        surfaceVariant: Color(hex: 0x49454F),
        onSurfaceVariant: Color(hex: 0xCAC4D0),
        outline: Color(hex: 0x938F99),
        outlineVariant: Color(hex: 0x49454F)
    )

    static let light = MobaideColorScheme(
        primary: MobaideColors.purple40,
        secondary: MobaideColors.purpleGrey40,
        tertiary: MobaideColors.pink40,
        error: Color(hex: 0xB3261E),
        errorContainer: Color(hex: 0xF9DEDC),
        onErrorContainer: Color(hex: 0x410E0B),
        surfaceVariant: Color(hex: 0xE7E0EC),
        onSurfaceVariant: Color(hex: 0x49454F),
        outline: Color(hex: 0x79747E),
        outlineVariant: Color(hex: 0xCAC4D0)
    )
}

extension EnvironmentValues {
    @Entry var mobaideColors: MobaideColorScheme = .dark
}

private struct MobaideThemeModifier: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme

    func body(content: Content) -> some View {
        content
            .environment(\.mobaideColors, colorScheme == .dark ? .dark : .light)
            .tint(colorScheme == .dark ? MobaideColorScheme.dark.primary : MobaideColorScheme.light.primary)
    }
}

extension View {
    func mobaideTheme() -> some View {
        modifier(MobaideThemeModifier())
    }
}
