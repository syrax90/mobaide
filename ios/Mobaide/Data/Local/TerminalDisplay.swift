import Foundation

/// Mirror of Android `TerminalDisplay.kt`. Raw values match the Kotlin
/// enum constant names persisted by Android (`mode.name`).
enum TerminalDisplayMode: String, Codable, Equatable, Sendable {
    case full = "Full"
    case `static` = "Static"
}

struct TerminalDisplayConfig: Equatable, Sendable {
    let mode: TerminalDisplayMode
    let staticColumns: Int
}

enum TerminalDisplayConstants {
    static let staticColumnsDefault = 120
    static let staticColumnsMin = 60
    static let staticColumnsMax = 240
    static let staticColumnsStep = 10
}
