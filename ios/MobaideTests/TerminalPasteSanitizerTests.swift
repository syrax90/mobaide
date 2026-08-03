import XCTest
@testable import Mobaide

/// Port of Android `TerminalEmulatorPasteTest`, case for case. The two clients
/// must put the same bytes on the wire for the same clipboard, so these
/// assertions are deliberately written against the same inputs and expectations.
final class TerminalPasteSanitizerTests: XCTestCase {
    private let esc = "\u{1B}"

    private func writes(_ text: String, bracketed: Bool = false) -> [String] {
        TerminalPaste.writes(for: text, bracketed: bracketed)
            .map { String(decoding: $0, as: UTF8.self) }
    }

    private func concatenated(_ text: String, bracketed: Bool = false) -> String {
        writes(text, bracketed: bracketed).joined()
    }

    private func occurrences(of needle: String, in haystack: String) -> Int {
        haystack.components(separatedBy: needle).count - 1
    }

    func testSingleLineBracketedOffWritesTextVerbatimWithoutMarkers() {
        XCTAssertEqual(
            concatenated("hello world"),
            "hello world",
            "with DECSET 2004 off the body must pass through unwrapped — markers would surface as literal text on non-bracketed shells"
        )
    }

    func testLfAndCrlfConvertToCr() {
        XCTAssertEqual(
            concatenated("a\nb\r\nc"),
            "a\rb\rc",
            "every LF and CRLF must become a single CR — terminal line discipline expects carriage returns, and CRLF must not produce a double return"
        )
    }

    func testBracketedModeOnWrapsInExactlyThreeWrites() {
        XCTAssertEqual(
            writes("a\nb", bracketed: true),
            ["\(esc)[200~", "a\rb", "\(esc)[201~"],
            "bracketed paste is exactly three ordered writes: open marker, sanitized body, close marker"
        )
    }

    func testEscAndC1ControlsStripped() {
        XCTAssertEqual(
            concatenated("a\(esc)[31mred\u{90}b"),
            "a[31mredb",
            "ESC and the C1 range are removed before anything else runs"
        )
    }

    func testEmbeddedEndMarkerCannotTerminateBracketedPasteEarly() {
        let output = writes("evil\(esc)[201~; rm -rf ~\n", bracketed: true)

        // The ESC strip runs before wrapping, so a clipboard that embeds a live
        // ESC[201~ cannot close the bracket early and have the tail execute as
        // direct input — it degrades to the literal text "[201~".
        XCTAssertEqual(output, ["\(esc)[200~", "evil[201~; rm -rf ~\r", "\(esc)[201~"])
        XCTAssertEqual(
            occurrences(of: "\(esc)[201~", in: output.joined()),
            1,
            "exactly one live end marker may reach the PTY — the wrap's own"
        )
    }

    func testPreWrappedClipboardProducesNoNestedLiveMarkers() {
        // Clipboard already carrying a bracketed-paste block (e.g. copied from a
        // terminal transcript): the embedded markers must be defanged, leaving
        // the encoder's own wrap as the only live pair.
        let output = concatenated("\(esc)[200~body\(esc)[201~", bracketed: true)

        XCTAssertEqual(occurrences(of: "\(esc)[200~", in: output), 1)
        XCTAssertEqual(occurrences(of: "\(esc)[201~", in: output), 1)
        XCTAssertEqual(output, "\(esc)[200~[200~body[201~\(esc)[201~")
    }

    func testBracketedOffStrippedMultilineNeverEmitsEsc() {
        let output = concatenated("line1\nline2\(esc)[H")

        XCTAssertEqual(output, "line1\rline2[H")
        XCTAssertFalse(output.contains(esc), "with 2004 off no ESC byte may ever reach the PTY")
    }

    func testEmptyAndFullyStrippedInputProduceNoWrites() {
        XCTAssertTrue(TerminalPaste.writes(for: "", bracketed: true).isEmpty)
        XCTAssertTrue(
            TerminalPaste.writes(for: esc, bracketed: true).isEmpty,
            "a clipboard that sanitizes away to nothing must not emit a bare bracketed-paste wrap"
        )
    }

    /// The strip must complete before the newline pass runs. Fusing them into a
    /// single walk looks equivalent but is not: it would emit the CR, drop the
    /// ESC, then meet a bare LF and emit a *second* CR — one pasted line
    /// becoming two commands.
    func testStripRunsAsAFullPassBeforeNewlineCollapsing() {
        XCTAssertEqual(
            concatenated("a\r\(esc)\nb"),
            "a\rb",
            "after stripping, CR ESC LF is a single CRLF — not a CR followed by a lone LF"
        )
    }

    func testOversizedClipboardIsRefusedRatherThanTruncated() {
        let oversized = String(repeating: "a", count: TerminalPaste.maxPasteBytes + 1)

        XCTAssertTrue(
            TerminalPaste.writes(for: oversized, bracketed: true).isEmpty,
            "a frame past the agent's relay cap would close its tunnel for every session on that agent"
        )
        XCTAssertFalse(
            TerminalPaste.writes(for: String(repeating: "a", count: TerminalPaste.maxPasteBytes), bracketed: true).isEmpty,
            "the cap itself must still be pasteable"
        )
    }

    func testLoneCarriageReturnSurvives() {
        XCTAssertEqual(
            concatenated("a\rb"),
            "a\rb",
            "only LF and CRLF are rewritten; a CR the user copied is already the byte the PTY wants"
        )
    }
}
