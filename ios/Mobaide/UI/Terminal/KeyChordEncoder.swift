import Foundation

/// A keyboard chord: at most two of Ctrl/Alt/Shift over a single base key from
/// `KeyChordKeys`. Persisted as a compact token ("ctrl+shift+i") so storage
/// never depends on this type — see `token` / `parse` (Android `KeyChord`).
struct KeyChord: Equatable, Hashable, Sendable {
    var ctrl = false
    var alt = false
    var shift = false
    let key: String

    /// Canonical storage token: modifiers in ctrl, alt, shift order, key last, lowercase.
    var token: String {
        var result = ""
        if ctrl { result += "ctrl+" }
        if alt { result += "alt+" }
        if shift { result += "shift+" }
        return result + key
    }

    /// Human label in conventional order, e.g. "Ctrl+Shift+I", "Alt+F4".
    var display: String {
        var result = ""
        if ctrl { result += "Ctrl+" }
        if alt { result += "Alt+" }
        if shift { result += "Shift+" }
        return result + KeyChordKeys.displayName(key)
    }

    /// Inverse of `token`. Returns nil for anything invalid — unknown key or
    /// modifier, duplicate modifier, all three modifiers, blank — so callers can
    /// silently drop stale or forward-incompatible persisted tokens.
    static func parse(_ token: String) -> KeyChord? {
        let parts = token.split(separator: "+", omittingEmptySubsequences: false).map(String.init)
        guard let key = parts.last, KeyChordKeys.all.contains(key) else { return nil }
        var ctrl = false
        var alt = false
        var shift = false
        for mod in parts.dropLast() {
            switch mod {
            case "ctrl": if ctrl { return nil } else { ctrl = true }
            case "alt": if alt { return nil } else { alt = true }
            case "shift": if shift { return nil } else { shift = true }
            default: return nil
            }
        }
        if ctrl && alt && shift { return nil }
        return KeyChord(ctrl: ctrl, alt: alt, shift: shift, key: key)
    }
}

/// The base-key domain of the shortcut picker, as canonical lowercase tokens.
enum KeyChordKeys {
    static let letters: [String] = (UInt8(ascii: "a")...UInt8(ascii: "z")).map { String(UnicodeScalar($0)) }
    static let digits: [String] = (0...9).map(String.init)
    static let functionKeys: [String] = (1...12).map { "f\($0)" }

    // Esc is deliberately absent: it is already a control-row key, and Ctrl/Alt+Esc
    // are window-manager chords with no terminal meaning.
    static let specials: [String] = ["tab", "enter", "space", "backspace"]

    static let all: Set<String> = Set(letters + digits + functionKeys + specials)

    static func displayName(_ key: String) -> String {
        specials.contains(key) ? key.prefix(1).uppercased() + key.dropFirst() : key.uppercased()
    }
}

/// The exact wire form of an encoded chord (Android `ChordEncoding`).
///
/// `summary` renders the bytes for the picker preview ("0x09", "ESC 0x01",
/// "ESC [ 105 ; 6 u"), `usesCsiU` is the single source of truth for the
/// "may not work in every CLI" hint, and `sameAsPlainKey` discloses when a
/// chord collapses onto an unmodified special's byte (Ctrl+I → Tab,
/// Ctrl+M → Enter).
struct ChordEncoding: Equatable, Sendable {
    let bytes: [UInt8]
    let summary: String
    let usesCsiU: Bool
    let sameAsPlainKey: String?
}

/// Legacy-first chord-to-bytes encoder — byte-identical port of Android
/// `KeyChordEncoder` (the `KeyChordEncoderTest` matrix is the shared oracle).
///
/// Decision procedure, in order:
///  1. F-keys always use their xterm forms — plain F1–F4 = SS3 `ESC O P..S`,
///     plain F5–F12 = `CSI 15/17/18/19/20/21/23/24 ~`, and any modifier combo
///     switches to `CSI 1;m P..S` / `CSI n;m ~` with m = 1+shift(1)+alt(2)+ctrl(4).
///     Never CSI-u, never marked risky.
///  2. Other keys compute a legacy core from (ctrl, shift, key): Ctrl+letter = C0
///     (cp AND 0x1F), Shift+letter = uppercase, Shift+Tab = `CSI Z`,
///     Ctrl+Space = NUL, Ctrl+Backspace = BS. The core FAILS whenever legacy
///     encoding would silently drop a requested modifier (Ctrl+Shift+letter,
///     any modified digit, Ctrl+Tab, modified Enter, Shift+Space/Backspace).
///  3. Alt is an ESC prefix, composable only over a single-byte core
///     (Alt+X = `ESC x`, Ctrl+Alt+A = `ESC 0x01`); over the multi-byte `CSI Z`
///     core it cannot compose.
///  4. Anything rule 2 or 3 rejects falls back to kitty CSI-u
///     `ESC [ <codepoint> ; <m> u` (lowercase-letter codepoint) and is flagged
///     `usesCsiU` — CLIs opt into that encoding, so it may echo as literal
///     text in legacy-mode terminals.
enum KeyChordEncoder {

    static func encode(_ chord: KeyChord) -> ChordEncoding? {
        guard KeyChordKeys.all.contains(chord.key) else { return nil }
        if chord.ctrl && chord.alt && chord.shift { return nil }
        let mods = 1 + (chord.shift ? 1 : 0) + (chord.alt ? 2 : 0) + (chord.ctrl ? 4 : 0)
        let raw = KeyChordKeys.functionKeys.contains(chord.key)
            ? functionKeyForm(chord, mods: mods)
            : legacyOrCsiU(chord, mods: mods)
        return ChordEncoding(
            bytes: raw.bytes,
            summary: raw.summary,
            usesCsiU: raw.usesCsiU,
            sameAsPlainKey: plainKeyCollision(chord, bytes: raw.bytes)
        )
    }

    private struct Raw {
        let bytes: [UInt8]
        let summary: String
        var usesCsiU = false
    }

    private static let ss3Finals: [String: String] = ["f1": "P", "f2": "Q", "f3": "R", "f4": "S"]
    private static let tildeNumbers: [String: Int] = [
        "f5": 15, "f6": 17, "f7": 18, "f8": 19,
        "f9": 20, "f10": 21, "f11": 23, "f12": 24,
    ]

    private static func functionKeyForm(_ chord: KeyChord, mods: Int) -> Raw {
        let modified = chord.ctrl || chord.alt || chord.shift
        if let final = ss3Finals[chord.key] {
            return modified
                ? ascii("[1;\(mods)\(final)", summary: "ESC [ 1 ; \(mods) \(final)")
                : ascii("O\(final)", summary: "ESC O \(final)")
        }
        let n = tildeNumbers[chord.key]!
        return modified
            ? ascii("[\(n);\(mods)~", summary: "ESC [ \(n) ; \(mods) ~")
            : ascii("[\(n)~", summary: "ESC [ \(n) ~")
    }

    private static func legacyOrCsiU(_ chord: KeyChord, mods: Int) -> Raw {
        guard let core = legacyCore(chord) else { return csiU(chord.key, mods: mods) }
        if !chord.alt { return core }
        if core.bytes.count == 1 {
            return Raw(bytes: [0x1B] + core.bytes, summary: "ESC \(core.summary)")
        }
        return csiU(chord.key, mods: mods)
    }

    private static func legacyCore(_ chord: KeyChord) -> Raw? {
        if KeyChordKeys.letters.contains(chord.key) {
            let code = UInt8(chord.key.unicodeScalars.first!.value)
            switch (chord.ctrl, chord.shift) {
            case (true, true): return nil
            case (true, false): return singleByte(code & 0x1F)
            case (false, true): return singleByte(code - 0x20)
            case (false, false): return singleByte(code)
            }
        }
        if KeyChordKeys.digits.contains(chord.key) {
            if chord.ctrl || chord.shift { return nil }
            return singleByte(UInt8(chord.key.unicodeScalars.first!.value))
        }
        switch chord.key {
        case "tab":
            if chord.ctrl { return nil }
            return chord.shift ? ascii("[Z", summary: "ESC [ Z") : singleByte(0x09)
        case "enter":
            return (chord.ctrl || chord.shift) ? nil : singleByte(0x0D)
        case "space":
            if chord.shift { return nil }
            return chord.ctrl ? singleByte(0x00) : singleByte(0x20)
        case "backspace":
            if chord.shift { return nil }
            return chord.ctrl ? singleByte(0x08) : singleByte(0x7F)
        default:
            return nil
        }
    }

    private static func csiU(_ key: String, mods: Int) -> Raw {
        let cp: Int
        switch key {
        case "tab": cp = 9
        case "enter": cp = 13
        case "space": cp = 32
        case "backspace": cp = 127
        default: cp = Int(key.unicodeScalars.first!.value)
        }
        return Raw(
            bytes: [0x1B] + Array("[\(cp);\(mods)u".utf8),
            summary: "ESC [ \(cp) ; \(mods) u",
            usesCsiU: true
        )
    }

    private static func plainKeyCollision(_ chord: KeyChord, bytes: [UInt8]) -> String? {
        guard bytes.count == 1 else { return nil }
        let name: String
        switch bytes[0] {
        case 0x09: name = "Tab"
        case 0x0D: name = "Enter"
        case 0x20: name = "Space"
        case 0x7F: name = "Backspace"
        default: return nil
        }
        let isThatPlainSpecial = !chord.ctrl && !chord.alt && !chord.shift
            && KeyChordKeys.displayName(chord.key) == name
        return isThatPlainSpecial ? nil : name
    }

    private static func singleByte(_ b: UInt8) -> Raw {
        Raw(bytes: [b], summary: String(format: "0x%02X", b))
    }

    /// `afterEsc` is the sequence body following the leading ESC byte.
    private static func ascii(_ afterEsc: String, summary: String) -> Raw {
        Raw(bytes: [0x1B] + Array(afterEsc.utf8), summary: summary)
    }
}
