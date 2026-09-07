import SwiftUI
import UIKit

/// The web split: Departure Mono for chrome (titles, labels, chips, buttons,
/// section headings), the system face for body copy. The chrome face is
/// sized on its 11px grid (11 / 22 / 33) wherever the web is.
enum MobaideTypography {
    static let chromeFamily = "DepartureMono-Regular"
    static let chromeFamilyName = "Departure Mono"

    /// Resolved once; a missing `UIAppFonts` entry degrades to the platform
    /// monospace instead of the system default (which would silently look like
    /// nothing happened).
    static let chromeAvailable: Bool = UIFont(name: chromeFamily, size: 11) != nil

    static func chrome(_ size: CGFloat, relativeTo style: Font.TextStyle) -> Font {
        chromeAvailable
            ? .custom(chromeFamily, size: size, relativeTo: style)
            : .system(size: size, design: .monospaced)
    }

    static let chromeSmall = chrome(11, relativeTo: .caption)
    static let chromeBody = chrome(13, relativeTo: .footnote)
    static let dialogTitle = chrome(18, relativeTo: .title3)
    static let pageTitle = chrome(22, relativeTo: .title2)
    static let display = chrome(33, relativeTo: .largeTitle)

    static let terminal = Font.system(.body, design: .monospaced)
    static let code = Font.system(size: 14, design: .monospaced)
    static let otpDigit = chrome(22, relativeTo: .title2)
    static let bodyLarge = Font.system(size: 16)
}
