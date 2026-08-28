import Foundation

/// The web `themeStore` preference (`localStorage["mobaide-theme"]`): an
/// explicit light or dark, or `system` to follow the OS.
enum ThemePreference: String, CaseIterable, Equatable, Sendable {
    case system
    case light
    case dark
}

extension ThemePreference {
    /// Unknown or absent stored values coerce to `.system`, the shipped
    /// default and the web's sanitising merge.
    init(storage raw: String?) {
        self = raw.flatMap(ThemePreference.init(rawValue:)) ?? .system
    }
}
