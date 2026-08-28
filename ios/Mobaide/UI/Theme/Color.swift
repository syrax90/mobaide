import SwiftUI

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0,
            opacity: opacity
        )
    }
}

/// Colours that stay fixed in both themes: the terminal grid and the
/// auto-scan log pane are always dark on every client, because an ANSI
/// palette is a matched set with its background (`web/src/terminal/terminalTheme.ts`).
enum MobaideColors {
    static let terminalBackground = Color(hex: 0x12141A)
    static let terminalForeground = Color(hex: 0xE6E8EE)
    static let terminalCursor = Color(hex: 0x4DA3FF)

    static let logBackground = Color(hex: 0x1E1E1E)
    static let logForeground = Color(hex: 0xD4D4D4)
    static let logFrame = Color(hex: 0x232323)

    // Terminal text selection. The highlight is composited *over* the glyphs
    // (it is a subview, while SwiftTerm draws the text into the view's own
    // layer), so it must stay translucent or it would hide what it selects.
    static let terminalSelectionHighlight = Color(hex: 0x4A9EFF)
    static let terminalSelectionHandle = Color(hex: 0x4A9EFF)
    static let terminalSelectionHighlightOpacity = 0.32

    /// The 16 standard ANSI palette entries (indices 0-15). Shared with
    /// Android and the web client; pinned by `tests/protocol_fixtures/ansi/`.
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
}
