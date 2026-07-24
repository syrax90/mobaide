import Foundation

/// How the terminal surface relates to the device viewport (Android
/// `TerminalDisplayMode`).
///
/// `full` — column count auto-fits the viewport, recomputed on every layout
/// pass. `static` — column count is fixed at `TerminalDisplayConfig.staticColumns`
/// regardless of orientation; the surface is wider than the viewport and the
/// user pans horizontally.
enum TerminalDisplayMode: String, CaseIterable, Equatable, Sendable {
    case full
    case `static`
}

extension TerminalDisplayMode {
    /// Unknown or absent stored values coerce to `.static` — the shipped
    /// default, matching Android's `PreferencesStore` fallback.
    init(storage raw: String?) {
        self = raw.flatMap(TerminalDisplayMode.init(rawValue:)) ?? .static
    }
}

struct TerminalDisplayConfig: Equatable, Sendable {
    var mode: TerminalDisplayMode
    var staticColumns: Int
}

enum TerminalDisplayDefaults {
    static let staticColumns = 120
    static let minColumns = 60
    static let maxColumns = 240
    static let columnsStep = 10

    static func clampColumns(_ value: Int) -> Int {
        min(max(value, minColumns), maxColumns)
    }
}
