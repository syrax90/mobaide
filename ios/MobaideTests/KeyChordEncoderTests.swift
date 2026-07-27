import XCTest
@testable import Mobaide

/// Pins the `KeyChordEncoder` wire matrix: legacy-first byte forms, the exact
/// preview `ChordEncoding.summary` strings, the `usesCsiU` risky flag, and the
/// `sameAsPlainKey` disclosure (with its plain-special self-suppression). Also
/// covers the `KeyChord` token round-trip and `KeyChord.parse` rejection rules
/// the recents pipeline relies on to drop stale persisted tokens silently.
///
/// Byte-identical mirror of Android `KeyChordEncoderTest` — the shared oracle.
final class KeyChordEncoderTests: XCTestCase {

    private func chord(
        _ key: String,
        ctrl: Bool = false,
        alt: Bool = false,
        shift: Bool = false
    ) -> KeyChord {
        KeyChord(ctrl: ctrl, alt: alt, shift: shift, key: key)
    }

    private func assertEncodes(
        _ chord: KeyChord,
        bytes expectedBytes: [UInt8],
        summary expectedSummary: String,
        usesCsiU: Bool = false,
        sameAsPlainKey: String? = nil,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        guard let encoding = KeyChordEncoder.encode(chord) else {
            return XCTFail("\(chord.display) must encode", file: file, line: line)
        }
        XCTAssertEqual(encoding.bytes, expectedBytes, "\(chord.display) bytes", file: file, line: line)
        XCTAssertEqual(encoding.summary, expectedSummary, "\(chord.display) summary", file: file, line: line)
        XCTAssertEqual(encoding.usesCsiU, usesCsiU, "\(chord.display) usesCsiU", file: file, line: line)
        XCTAssertEqual(encoding.sameAsPlainKey, sameAsPlainKey, "\(chord.display) sameAsPlainKey", file: file, line: line)
    }

    func testPlainAndShiftedKeysSendLiteralBytes() {
        assertEncodes(chord("a"), bytes: [0x61], summary: "0x61")
        assertEncodes(chord("i", shift: true), bytes: [0x49], summary: "0x49")
        assertEncodes(chord("2"), bytes: [0x32], summary: "0x32")
    }

    func testCtrlChordsSendLegacyC0Bytes() {
        assertEncodes(chord("a", ctrl: true), bytes: [0x01], summary: "0x01")
        assertEncodes(chord("space", ctrl: true), bytes: [0x00], summary: "0x00")
        assertEncodes(chord("backspace", ctrl: true), bytes: [0x08], summary: "0x08")
    }

    func testCtrlIAndCtrlMSendLegacyBytesWithPlainKeyDisclosure() {
        assertEncodes(chord("i", ctrl: true), bytes: [0x09], summary: "0x09", sameAsPlainKey: "Tab")
        assertEncodes(chord("m", ctrl: true), bytes: [0x0D], summary: "0x0D", sameAsPlainKey: "Enter")
    }

    func testPlainSpecialsSuppressTheirOwnDisclosure() {
        assertEncodes(chord("tab"), bytes: [0x09], summary: "0x09")
        assertEncodes(chord("enter"), bytes: [0x0D], summary: "0x0D")
        assertEncodes(chord("space"), bytes: [0x20], summary: "0x20")
        assertEncodes(chord("backspace"), bytes: [0x7F], summary: "0x7F")
    }

    func testAltChordsPrefixEscOverSingleByteCores() {
        assertEncodes(chord("x", alt: true), bytes: [0x1B, 0x78], summary: "ESC 0x78")
        assertEncodes(chord("a", alt: true, shift: true), bytes: [0x1B, 0x41], summary: "ESC 0x41")
        assertEncodes(chord("a", ctrl: true, alt: true), bytes: [0x1B, 0x01], summary: "ESC 0x01")
        assertEncodes(chord("5", alt: true), bytes: [0x1B, 0x35], summary: "ESC 0x35")
        assertEncodes(chord("backspace", alt: true), bytes: [0x1B, 0x7F], summary: "ESC 0x7F")
    }

    func testShiftTabSendsCsiZ() {
        assertEncodes(chord("tab", shift: true), bytes: [0x1B, 0x5B, 0x5A], summary: "ESC [ Z")
    }

    func testPlainFunctionKeysUseXtermSs3AndTildeForms() {
        assertEncodes(chord("f1"), bytes: [0x1B, 0x4F, 0x50], summary: "ESC O P")
        assertEncodes(chord("f5"), bytes: [0x1B, 0x5B, 0x31, 0x35, 0x7E], summary: "ESC [ 15 ~")
        assertEncodes(chord("f12"), bytes: [0x1B, 0x5B, 0x32, 0x34, 0x7E], summary: "ESC [ 24 ~")
    }

    func testModifiedFunctionKeysUseXtermModifierFormsNeverCsiU() {
        assertEncodes(
            chord("f1", ctrl: true),
            bytes: [0x1B, 0x5B, 0x31, 0x3B, 0x35, 0x50],
            summary: "ESC [ 1 ; 5 P"
        )
        assertEncodes(
            chord("f5", shift: true),
            bytes: [0x1B, 0x5B, 0x31, 0x35, 0x3B, 0x32, 0x7E],
            summary: "ESC [ 15 ; 2 ~"
        )
    }

    func testInexpressibleChordsFallBackToCsiUAndAreFlaggedRisky() {
        assertEncodes(
            chord("i", ctrl: true, shift: true),
            bytes: [0x1B, 0x5B, 0x31, 0x30, 0x35, 0x3B, 0x36, 0x75],
            summary: "ESC [ 105 ; 6 u",
            usesCsiU: true
        )
        assertEncodes(
            chord("2", ctrl: true),
            bytes: [0x1B, 0x5B, 0x35, 0x30, 0x3B, 0x35, 0x75],
            summary: "ESC [ 50 ; 5 u",
            usesCsiU: true
        )
        assertEncodes(
            chord("2", shift: true),
            bytes: [0x1B, 0x5B, 0x35, 0x30, 0x3B, 0x32, 0x75],
            summary: "ESC [ 50 ; 2 u",
            usesCsiU: true
        )
        assertEncodes(
            chord("enter", shift: true),
            bytes: [0x1B, 0x5B, 0x31, 0x33, 0x3B, 0x32, 0x75],
            summary: "ESC [ 13 ; 2 u",
            usesCsiU: true
        )
        assertEncodes(
            chord("enter", ctrl: true),
            bytes: [0x1B, 0x5B, 0x31, 0x33, 0x3B, 0x35, 0x75],
            summary: "ESC [ 13 ; 5 u",
            usesCsiU: true
        )
        assertEncodes(
            chord("tab", ctrl: true),
            bytes: [0x1B, 0x5B, 0x39, 0x3B, 0x35, 0x75],
            summary: "ESC [ 9 ; 5 u",
            usesCsiU: true
        )
        assertEncodes(
            chord("tab", alt: true, shift: true),
            bytes: [0x1B, 0x5B, 0x39, 0x3B, 0x34, 0x75],
            summary: "ESC [ 9 ; 4 u",
            usesCsiU: true
        )
        assertEncodes(
            chord("space", ctrl: true, shift: true),
            bytes: [0x1B, 0x5B, 0x33, 0x32, 0x3B, 0x36, 0x75],
            summary: "ESC [ 32 ; 6 u",
            usesCsiU: true
        )
        assertEncodes(
            chord("backspace", shift: true),
            bytes: [0x1B, 0x5B, 0x31, 0x32, 0x37, 0x3B, 0x32, 0x75],
            summary: "ESC [ 127 ; 2 u",
            usesCsiU: true
        )
    }

    func testCtrlShiftIIsDistinctFromCtrlI() throws {
        let ctrlShiftI = try XCTUnwrap(KeyChordEncoder.encode(chord("i", ctrl: true, shift: true)))
        let ctrlI = try XCTUnwrap(KeyChordEncoder.encode(chord("i", ctrl: true)))
        XCTAssertNotEqual(
            ctrlShiftI.bytes, ctrlI.bytes,
            "Ctrl+Shift+I must not collapse onto the Ctrl+I (= Tab) byte"
        )
    }

    func testEncodeReturnsNilForUnknownKeyOrAllThreeModifiers() {
        XCTAssertNil(KeyChordEncoder.encode(chord("esc", ctrl: true)))
        XCTAssertNil(KeyChordEncoder.encode(chord("a", ctrl: true, alt: true, shift: true)))
    }

    func testTokenRoundTripsThroughParse() {
        let chords = [
            chord("a"),
            chord("i", ctrl: true, shift: true),
            chord("f5", alt: true),
            chord("backspace", ctrl: true),
        ]
        for original in chords {
            XCTAssertEqual(KeyChord.parse(original.token), original)
        }
        XCTAssertEqual(chord("i", ctrl: true, shift: true).token, "ctrl+shift+i")
    }

    func testParseRejectsInvalidTokens() {
        for token in ["", "ctrl+", "ctrl+alt+shift+a", "meta+a", "ctrl+esc", "ctrl+ctrl+a"] {
            XCTAssertNil(KeyChord.parse(token), "\"\(token)\" must not parse")
        }
    }

    func testDisplayUsesConventionalModifierOrderAndKeyNames() {
        XCTAssertEqual(chord("i", ctrl: true, shift: true).display, "Ctrl+Shift+I")
        XCTAssertEqual(chord("f4", alt: true).display, "Alt+F4")
        XCTAssertEqual(chord("space", ctrl: true).display, "Ctrl+Space")
        XCTAssertEqual(chord("backspace").display, "Backspace")
    }
}
