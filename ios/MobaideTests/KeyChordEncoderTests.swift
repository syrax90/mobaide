import XCTest
@testable import Mobaide

/// Structural properties of `KeyChordEncoder` that a case table cannot express.
/// The wire matrix itself lives in `tests/protocol_fixtures/keys/key_chords.json`
/// and is asserted by `KeyChordFixtureTests`, the Android `KeyChordFixtureTest`
/// and the web `keyChord.test.ts`.
final class KeyChordEncoderTests: XCTestCase {

    private func chord(
        _ key: String,
        ctrl: Bool = false,
        alt: Bool = false,
        shift: Bool = false
    ) -> KeyChord {
        KeyChord(ctrl: ctrl, alt: alt, shift: shift, key: key)
    }

    private let legalModifierSubsets: [(ctrl: Bool, alt: Bool, shift: Bool)] = [
        (false, false, false),
        (true, false, false),
        (false, true, false),
        (false, false, true),
        (true, true, false),
        (true, false, true),
        (false, true, true),
    ]

    func testCtrlShiftIIsDistinctFromCtrlI() throws {
        let ctrlShiftI = try XCTUnwrap(KeyChordEncoder.encode(chord("i", ctrl: true, shift: true)))
        let ctrlI = try XCTUnwrap(KeyChordEncoder.encode(chord("i", ctrl: true)))
        XCTAssertNotEqual(
            ctrlShiftI.bytes, ctrlI.bytes,
            "Ctrl+Shift+I must not collapse onto the Ctrl+I (= Tab) byte"
        )
    }

    /// Every chord the picker can compose has to encode, or a user could select a
    /// combination the Send button then silently drops.
    func testEveryChordInTheDomainEncodes() throws {
        for key in KeyChordKeys.all {
            for subset in legalModifierSubsets {
                let composed = chord(key, ctrl: subset.ctrl, alt: subset.alt, shift: subset.shift)
                let encoding = try XCTUnwrap(
                    KeyChordEncoder.encode(composed), "\(composed.token) must encode"
                )
                XCTAssertFalse(encoding.bytes.isEmpty, "\(composed.token) bytes")
            }
        }
    }

    /// A CSI-u sequence carries the modifier set explicitly, so unlike the legacy
    /// forms (where Ctrl+I and Tab share a byte) two distinct chords can never
    /// collapse onto one sequence.
    func testCsiUSequencesAreUniquePerChord() {
        var seen: [String: String] = [:]
        for key in KeyChordKeys.all {
            for subset in legalModifierSubsets {
                let composed = chord(key, ctrl: subset.ctrl, alt: subset.alt, shift: subset.shift)
                guard let encoding = KeyChordEncoder.encode(composed), encoding.usesCsiU else {
                    continue
                }
                let hex = Data(encoding.bytes).hexString
                XCTAssertNil(
                    seen[hex],
                    "\(hex) is emitted by both \(seen[hex] ?? "") and \(composed.token)"
                )
                seen[hex] = composed.token
            }
        }
    }
}
