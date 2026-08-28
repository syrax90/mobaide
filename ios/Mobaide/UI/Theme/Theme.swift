import SwiftUI
import UIKit

/// The web client's "modern pixel" tokens (`web/src/styles/theme.css`), one
/// value per CSS custom property. The dark block is `site.css` verbatim; the
/// light block is the SPA's derived variant. A token change there must be
/// mirrored here (docs/website.md).
struct MobaideColorScheme: Equatable, Sendable {
    /// Fill and ring colours only, in both themes: on dark they fail WCAG AA
    /// as text (~2.6:1), so text takes `accent` instead.
    static let brand = Color(hex: 0x6008AC)
    static let brand2 = Color(hex: 0x6650A4)
    static let btnShadow = Color(hex: 0x2B0450)
    static let dangerFill = Color(hex: 0x8C1D18)
    static let dangerFillShadow = Color(hex: 0x4A0F0C)
    /// The wordmark's plate: the mark is dark ink drawn for a light background.
    static let plate = Color(hex: 0xF4F4F4)
    static let modalShadow = Color.black

    let bg: Color
    let bg2: Color
    let surface: Color
    let surface2: Color
    let accent: Color
    let text: Color
    let textMuted: Color
    /// Interactive control boundaries ring in `gray` (>= 3:1), never `rule`.
    let gray: Color
    let danger: Color
    let ok: Color
    let warning: Color
    /// Decorative frames, chips and banners only.
    let rule: Color
    let scrim: Color
    let headBg: Color

    static let dark = MobaideColorScheme(
        bg: 0x121212, bg2: 0x171717, surface: 0x1E1E1E, surface2: 0x232323,
        accent: 0xD0BCFF, text: 0xE9E5EF, textMuted: 0xCCC2DC, gray: 0x8B8B8B,
        danger: 0xF2B8B5, ok: 0x23D18B, warning: 0xE3B341, rule: 0x625B71,
        scrim: Color.black.opacity(0.72), headBg: Color(hex: 0x121212, opacity: 0.92)
    )

    static let light = MobaideColorScheme(
        bg: 0xF4F4F4, bg2: 0xFDFCFF, surface: 0xEFECF3, surface2: 0xDCD7E2,
        accent: 0x6008AC, text: 0x1C1B1F, textMuted: 0x49454F, gray: 0x666666,
        danger: 0x8C1D18, ok: 0x116A2E, warning: 0x8A6100, rule: 0xA099AB,
        scrim: Color.black.opacity(0.5), headBg: Color(hex: 0xF4F4F4, opacity: 0.92)
    )

    private init(
        bg: UInt32, bg2: UInt32, surface: UInt32, surface2: UInt32,
        accent: UInt32, text: UInt32, textMuted: UInt32, gray: UInt32,
        danger: UInt32, ok: UInt32, warning: UInt32, rule: UInt32,
        scrim: Color, headBg: Color
    ) {
        self.bg = Color(hex: bg)
        self.bg2 = Color(hex: bg2)
        self.surface = Color(hex: surface)
        self.surface2 = Color(hex: surface2)
        self.accent = Color(hex: accent)
        self.text = Color(hex: text)
        self.textMuted = Color(hex: textMuted)
        self.gray = Color(hex: gray)
        self.danger = Color(hex: danger)
        self.ok = Color(hex: ok)
        self.warning = Color(hex: warning)
        self.rule = Color(hex: rule)
        self.scrim = scrim
        self.headBg = headBg
    }
}

/// Semantic tone a display helper can return without knowing the theme;
/// views resolve it through the scheme in their environment.
enum PixelTone: Equatable, Sendable {
    case accent, ok, warning, danger, muted, brand
}

extension MobaideColorScheme {
    func color(_ tone: PixelTone) -> Color {
        switch tone {
        case .accent: return accent
        case .ok: return ok
        case .warning: return warning
        case .danger: return danger
        case .muted: return gray
        case .brand: return Self.brand
        }
    }

    func ring(_ tone: PixelTone) -> Color {
        switch tone {
        case .accent, .brand: return Self.brand2
        case .ok: return ok
        case .warning: return warning
        case .danger: return danger
        case .muted: return rule
        }
    }

    /// A trait-resolving `UIColor` for UIKit appearance proxies, so one
    /// appearance serves both themes.
    static func dynamic(_ keyPath: KeyPath<MobaideColorScheme, Color>) -> UIColor {
        let darkColor = UIColor(dark[keyPath: keyPath])
        let lightColor = UIColor(light[keyPath: keyPath])
        return UIColor { traits in
            traits.userInterfaceStyle == .dark ? darkColor : lightColor
        }
    }
}

extension EnvironmentValues {
    @Entry var mobaideColors: MobaideColorScheme = .dark
}

private struct MobaideThemeModifier: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme

    func body(content: Content) -> some View {
        let scheme: MobaideColorScheme = colorScheme == .dark ? .dark : .light
        content
            .environment(\.mobaideColors, scheme)
            .tint(scheme.accent)
            .toggleStyle(.pixelCheckbox)
    }
}

extension View {
    func mobaideTheme() -> some View {
        modifier(MobaideThemeModifier())
    }
}

// MARK: - Appearance preference

extension ThemePreference {
    var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }

    var label: String {
        switch self {
        case .system: return "Auto"
        case .light: return "Light"
        case .dark: return "Dark"
        }
    }
}

/// Applied above `mobaideTheme()` so the forced scheme is what the theme reads.
struct ThemePreferenceModifier: ViewModifier {
    let themeStore: ThemeStore

    func body(content: Content) -> some View {
        content.preferredColorScheme(themeStore.preference.colorScheme)
    }
}
