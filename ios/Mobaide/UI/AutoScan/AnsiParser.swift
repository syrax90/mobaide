import SwiftUI

/// Integer RGB (0-255 per channel), `Color`-free so golden tests can assert
/// exact channels. `Color` is derived only at render time.
struct AnsiRGB: Equatable, Sendable {
    let r: Int
    let g: Int
    let b: Int

    init(r: Int, g: Int, b: Int) {
        self.r = r
        self.g = g
        self.b = b
    }

    init(hex: UInt32) {
        self.r = Int((hex >> 16) & 0xFF)
        self.g = Int((hex >> 8) & 0xFF)
        self.b = Int(hex & 0xFF)
    }
}

/// A maximal run of text sharing one SGR style.
struct AnsiRun: Equatable, Sendable {
    var text: String
    var foreground: AnsiRGB?
    var background: AnsiRGB?
    var bold: Bool = false
    var italic: Bool = false
    var underline: Bool = false
}

/// SGR (`ESC[…m`) → styled runs / `AttributedString`. A superset of the
/// Android `AnsiParser` (which handled only foreground 30-37/90-97 and 256
/// indices 0-15): this adds background, italic/underline, the 6×6×6 cube
/// (`index = 16 + 36r + 6g + b`, xterm channel levels `0,95,135,175,215,255`),
/// the 232-255 grayscale ramp, and 24-bit truecolor — the fuller color coverage
/// this port targets. Non-SGR CSI sequences are dropped.
///
/// `parse(_:)` is pure and `nonisolated`; the detail model builds
/// `AttributedString`s off the main actor via `Task.detached`.
enum AnsiParser {
    /// The 16 standard palette entries (indices 0-15), verbatim from
    /// `MobaideColors.ansiColors`.
    private static let basePalette: [AnsiRGB] = [
        AnsiRGB(hex: 0x000000), AnsiRGB(hex: 0xCD3131), AnsiRGB(hex: 0x0DBC79), AnsiRGB(hex: 0xE5E510),
        AnsiRGB(hex: 0x2472C8), AnsiRGB(hex: 0xBC3FBC), AnsiRGB(hex: 0x11A8CD), AnsiRGB(hex: 0xE5E5E5),
        AnsiRGB(hex: 0x666666), AnsiRGB(hex: 0xF14C4C), AnsiRGB(hex: 0x23D18B), AnsiRGB(hex: 0xF5F543),
        AnsiRGB(hex: 0x3B8EEA), AnsiRGB(hex: 0xD670D6), AnsiRGB(hex: 0x29B8DB), AnsiRGB(hex: 0xFFFFFF),
    ]

    static func parseRuns(_ input: String) -> [AnsiRun] {
        var runs: [AnsiRun] = []
        var buffer = ""
        var style = Style()
        let chars = Array(input)
        var i = 0

        func flush() {
            guard !buffer.isEmpty else { return }
            runs.append(style.run(buffer))
            buffer = ""
        }

        while i < chars.count {
            let c = chars[i]
            if c == "\u{1B}", i + 1 < chars.count, chars[i + 1] == "[" {
                var j = i + 2
                while j < chars.count, !isFinalByte(chars[j]) {
                    j += 1
                }
                // Unterminated CSI: drop the remainder (Android parity).
                if j >= chars.count { break }
                flush()
                if chars[j] == "m" {
                    applySGR(String(chars[(i + 2)..<j]), to: &style)
                }
                i = j + 1
            } else {
                buffer.append(c)
                i += 1
            }
        }
        flush()
        return runs
    }

    static func parse(_ input: String) -> AttributedString {
        var result = AttributedString()
        for run in parseRuns(input) {
            var piece = AttributedString(run.text)
            if let fg = run.foreground { piece.foregroundColor = Color(ansi: fg) }
            if let bg = run.background { piece.backgroundColor = Color(ansi: bg) }
            var intent: InlinePresentationIntent = []
            if run.bold { intent.insert(.stronglyEmphasized) }
            if run.italic { intent.insert(.emphasized) }
            if !intent.isEmpty { piece.inlinePresentationIntent = intent }
            if run.underline { piece.underlineStyle = Text.LineStyle.single }
            result.append(piece)
        }
        return result
    }

    private static func isFinalByte(_ c: Character) -> Bool {
        guard let scalar = c.unicodeScalars.first, c.unicodeScalars.count == 1 else { return false }
        return scalar.value >= 0x40 && scalar.value <= 0x7E
    }

    private static func applySGR(_ params: String, to style: inout Style) {
        let codes = params.isEmpty
            ? [0]
            : params.split(separator: ";", omittingEmptySubsequences: false).map { Int($0) ?? 0 }
        var j = 0
        while j < codes.count {
            let code = codes[j]
            switch code {
            case 0: style = Style()
            case 1: style.bold = true
            case 22: style.bold = false
            case 3: style.italic = true
            case 23: style.italic = false
            case 4: style.underline = true
            case 24: style.underline = false
            case 30...37: style.foreground = base(code - 30)
            case 90...97: style.foreground = base(8 + code - 90)
            case 39: style.foreground = nil
            case 40...47: style.background = base(code - 40)
            case 100...107: style.background = base(8 + code - 100)
            case 49: style.background = nil
            case 38, 48:
                j += consumeExtended(codes, at: j, foreground: code == 38, into: &style)
            default: break
            }
            j += 1
        }
    }

    /// Applies a `38`/`48` extended color; returns how many extra params it
    /// consumed beyond the `38`/`48` selector (0 when malformed).
    private static func consumeExtended(
        _ codes: [Int], at index: Int, foreground: Bool, into style: inout Style
    ) -> Int {
        guard index + 1 < codes.count else { return 0 }
        switch codes[index + 1] {
        case 5 where index + 2 < codes.count:
            assign(color256(codes[index + 2]), foreground: foreground, into: &style)
            return 2
        case 2 where index + 4 < codes.count:
            let rgb = AnsiRGB(
                r: clampChannel(codes[index + 2]),
                g: clampChannel(codes[index + 3]),
                b: clampChannel(codes[index + 4])
            )
            assign(rgb, foreground: foreground, into: &style)
            return 4
        default:
            return 0
        }
    }

    private static func assign(_ rgb: AnsiRGB, foreground: Bool, into style: inout Style) {
        if foreground { style.foreground = rgb } else { style.background = rgb }
    }

    private static func color256(_ n: Int) -> AnsiRGB {
        if n < 16 { return base(n) }
        if n < 232 {
            let c = n - 16
            return AnsiRGB(r: cubeLevel(c / 36), g: cubeLevel((c / 6) % 6), b: cubeLevel(c % 6))
        }
        if n < 256 {
            let gray = 8 + (n - 232) * 10
            return AnsiRGB(r: gray, g: gray, b: gray)
        }
        return base(7)
    }

    private static func cubeLevel(_ level: Int) -> Int { level == 0 ? 0 : 55 + level * 40 }

    private static func clampChannel(_ value: Int) -> Int { min(255, max(0, value)) }

    private static func base(_ index: Int) -> AnsiRGB { basePalette[min(15, max(0, index))] }

    private struct Style {
        var foreground: AnsiRGB?
        var background: AnsiRGB?
        var bold = false
        var italic = false
        var underline = false

        func run(_ text: String) -> AnsiRun {
            AnsiRun(
                text: text, foreground: foreground, background: background,
                bold: bold, italic: italic, underline: underline
            )
        }
    }
}

extension Color {
    init(ansi rgb: AnsiRGB) {
        self.init(
            red: Double(rgb.r) / 255.0,
            green: Double(rgb.g) / 255.0,
            blue: Double(rgb.b) / 255.0
        )
    }
}
