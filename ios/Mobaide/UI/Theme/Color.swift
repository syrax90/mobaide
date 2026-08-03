import SwiftUI

extension Color {
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0
        )
    }
}

/// Mirror of Android `ui/theme/Color.kt` — every hex value verbatim.
enum MobaideColors {
    // Material seed colors.
    static let purple80 = Color(hex: 0xD0BCFF)
    static let purpleGrey80 = Color(hex: 0xCCC2DC)
    static let pink80 = Color(hex: 0xEFB8C8)
    static let purple40 = Color(hex: 0x6650A4)
    static let purpleGrey40 = Color(hex: 0x625B71)
    static let pink40 = Color(hex: 0x7D5260)

    // Terminal base.
    static let terminalBackground = Color(hex: 0x1E1E1E)
    static let terminalForeground = Color(hex: 0xD4D4D4)

    // Terminal connection-overlay pills.
    static let terminalOverlayReconnecting = Color(hex: 0x202020)
    static let terminalOverlayReplaying = Color(hex: 0x2A4D6E)

    // Terminal text selection. The highlight is composited *over* the glyphs
    // (it is a subview, while SwiftTerm draws the text into the view's own
    // layer), so it must stay translucent or it would hide what it selects.
    static let terminalSelectionHighlight = Color(hex: 0x4A9EFF)
    static let terminalSelectionHandle = Color(hex: 0x4A9EFF)
    static let terminalSelectionHighlightOpacity = 0.32

    /// The 16 standard ANSI palette entries (indices 0-15).
    static let ansiColors: [Color] = [
        Color(hex: 0x000000), // black
        Color(hex: 0xCD3131), // red
        Color(hex: 0x0DBC79), // green
        Color(hex: 0xE5E510), // yellow
        Color(hex: 0x2472C8), // blue
        Color(hex: 0xBC3FBC), // magenta
        Color(hex: 0x11A8CD), // cyan
        Color(hex: 0xE5E5E5), // white
        Color(hex: 0x666666), // bright black
        Color(hex: 0xF14C4C), // bright red
        Color(hex: 0x23D18B), // bright green
        Color(hex: 0xF5F543), // bright yellow
        Color(hex: 0x3B8EEA), // bright blue
        Color(hex: 0xD670D6), // bright magenta
        Color(hex: 0x29B8DB), // bright cyan
        Color(hex: 0xFFFFFF), // bright white
    ]

    // Session-state colors.
    static let stateRunning = Color(hex: 0x4CAF50)
    static let stateStopped = Color(hex: 0x9E9E9E)
    static let stateCrashed = Color(hex: 0xF44336)
    static let stateStarting = Color(hex: 0xFFC107)
    static let stateStopping = Color(hex: 0xFF9800)

    static let warningYellow = Color(hex: 0xF9A825)
}
