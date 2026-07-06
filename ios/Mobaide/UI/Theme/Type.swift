import SwiftUI

/// Mirror of Android `ui/theme/Type.kt`: the platform monospace family for
/// terminal/code text plus the three overridden text roles.
enum MobaideTypography {
    /// `FontFamily.Monospace` parity — the platform monospaced face.
    static let terminal = Font.system(.body, design: .monospaced)
    static let code = Font.system(size: 14, design: .monospaced)
    static let otpDigit = Font.system(size: 24, weight: .medium, design: .monospaced)

    static let bodyLarge = Font.system(size: 16)
    static let titleLarge = Font.system(size: 22)
    static let labelSmall = Font.system(size: 11, weight: .medium)
}
