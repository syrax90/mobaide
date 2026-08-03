import Foundation

/// Clipboard-to-PTY encoder, a byte-exact mirror of Android
/// `TerminalEmulator.paste()` (`terminal-emulator/.../TerminalEmulator.java`).
///
/// The order is load-bearing and is the escape-injection defense: stripping ESC
/// and C1 *before* wrapping means clipboard text carrying a live `ESC[201~`
/// degrades to the literal `[201~` and cannot close the bracket early, leaving
/// its tail to execute as direct input (the MinTTY/Xshell CVE-2021-31701 class).
enum TerminalPaste {
    /// `ESC[200~` / `ESC[201~`. Spelled out rather than taken from
    /// `EscapeSequences.bracketedPasteStart`, which SwiftTerm declares as a
    /// mutable `public static var` that Swift 6 will not let us read across
    /// isolation domains. They are a frozen wire contract either way, and the
    /// composer's encoder in `TerminalSessionModel.sendInput` already spells
    /// them out identically.
    static let bracketedPasteStart: [UInt8] = [0x1B, 0x5B, 0x32, 0x30, 0x30, 0x7E]
    static let bracketedPasteEnd: [UInt8] = [0x1B, 0x5B, 0x32, 0x30, 0x31, 0x7E]

    /// Largest clipboard payload that may be put on the wire.
    ///
    /// A deliberate divergence from Android, which has no cap: the agent's
    /// relay tunnel is opened with `max_size=4 MB` (`relay/client.py`), and a
    /// single oversized frame trips `PayloadTooBig` and closes that tunnel with
    /// 1009 — taking down **every** session on the agent, not just this one. A
    /// megabyte is far past any real paste into a prompt, and refusing is much
    /// cheaper than the reconnect storm.
    static let maxPasteBytes = 1 << 20

    /// Drops ESC (0x1B) and the C1 range [0x80…0x9F], leaving everything else
    /// alone. Split out from `sanitize` because the composer's `m`-submit
    /// encoder needs the escape-injection strip without the newline rewrite.
    static func stripControls(_ text: String) -> String {
        var contiguous = text
        // `UIPasteboard.string` hands back a lazily-bridged NSString, whose
        // scalar view takes a slow non-contiguous path; one conversion up front
        // keeps the walk on the native UTF-8 fast path.
        contiguous.makeContiguousUTF8()
        var out = String.UnicodeScalarView()
        // An O(1) upper bound on the same storage — `unicodeScalars.count`
        // would be a second full pass over the payload.
        out.reserveCapacity(contiguous.utf8.count)
        for scalar in contiguous.unicodeScalars
        where scalar != "\u{1B}" && !(scalar.value >= 0x80 && scalar.value <= 0x9F) {
            out.append(scalar)
        }
        return String(out)
    }

    /// Step 1 — `stripControls`. Step 2 — collapse `\r\n` and `\n` to a single
    /// `\r`; a lone `\r` is left alone.
    ///
    /// Two ordered passes, exactly as Android runs them. A single fused walk is
    /// tempting but *not* equivalent: for `"\r" ESC "\n"` Android strips first
    /// and sees `\r\n`, one line break, while a fused walk emits the `\r`,
    /// drops the ESC, then meets a bare `\n` and emits a second `\r`.
    static func sanitize(_ text: String) -> String {
        let stripped = stripControls(text)
        var out = String.UnicodeScalarView()
        out.reserveCapacity(stripped.utf8.count)

        var iterator = stripped.unicodeScalars.makeIterator()
        var pending: Unicode.Scalar? = iterator.next()
        while let scalar = pending {
            pending = iterator.next()
            if scalar == "\r" {
                // CRLF is one line break, not two.
                if pending == "\n" { pending = iterator.next() }
                out.append("\r")
                continue
            }
            out.append(scalar == "\n" ? "\r" : scalar)
        }
        return String(out)
    }

    /// The writes to put on the wire for `text`.
    ///
    /// Returns one write, or — when the program enabled bracketed paste
    /// (DECSET 2004) — exactly three ordered writes: open marker, sanitized
    /// body, close marker. Android emits the same three and its test suite pins
    /// the count, so the shapes stay comparable across clients; the PTY
    /// reassembles them either way.
    static func writes(for text: String, bracketed: Bool) -> [[UInt8]] {
        let body = sanitize(text)
        guard !body.isEmpty else { return [] }
        let bodyBytes = Array(body.utf8)
        // Enforced here as well as at the call site, so the cap holds for every
        // caller rather than only the one that renders the toast.
        guard bodyBytes.count <= maxPasteBytes else { return [] }
        guard bracketed else { return [bodyBytes] }
        return [bracketedPasteStart, bodyBytes, bracketedPasteEnd]
    }
}
