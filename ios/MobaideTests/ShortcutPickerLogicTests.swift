import XCTest
@testable import Mobaide

/// The picker's extracted enable/derivation rules (`ShortcutPickerLogic`) and
/// the `SavedShortcutUi` label fallback. The view drives these statics
/// directly, so they carry the max-2-modifiers rule, the send gate, and the
/// preview captions the sheet renders.
final class ShortcutPickerLogicTests: XCTestCase {

    func testModifierChipEnabledCapsAtTwoButAlwaysAllowsDeselect() {
        XCTAssertTrue(ShortcutPickerLogic.modifierChipEnabled(selected: false, selectedCount: 0))
        XCTAssertTrue(ShortcutPickerLogic.modifierChipEnabled(selected: false, selectedCount: 1))
        XCTAssertFalse(
            ShortcutPickerLogic.modifierChipEnabled(selected: false, selectedCount: 2),
            "the third, unselected modifier disables once two are on"
        )
        XCTAssertTrue(
            ShortcutPickerLogic.modifierChipEnabled(selected: true, selectedCount: 2),
            "a selected chip stays enabled so it can be toggled off"
        )
    }

    func testSavedMatchUsesStructuralChordEquality() {
        let saved = [
            SavedShortcutUi(id: "s-1", chord: KeyChord(ctrl: true, key: "a"), name: "Alpha", risky: false),
            SavedShortcutUi(id: "s-2", chord: KeyChord(ctrl: true, shift: true, key: "i"), name: nil, risky: true),
        ]
        XCTAssertNil(ShortcutPickerLogic.savedMatch(chord: nil, in: saved))
        XCTAssertNil(
            ShortcutPickerLogic.savedMatch(chord: KeyChord(key: "a"), in: saved),
            "modifiers are part of the chord's identity"
        )
        XCTAssertEqual(
            ShortcutPickerLogic.savedMatch(chord: KeyChord(ctrl: true, shift: true, key: "i"), in: saved)?.id,
            "s-2"
        )
    }

    func testSendEnabledRequiresSessionGateAndComposedChord() {
        XCTAssertTrue(ShortcutPickerLogic.sendEnabled(gate: true, hasChord: true))
        XCTAssertFalse(ShortcutPickerLogic.sendEnabled(gate: false, hasChord: true))
        XCTAssertFalse(ShortcutPickerLogic.sendEnabled(gate: true, hasChord: false))
        XCTAssertFalse(ShortcutPickerLogic.sendEnabled(gate: false, hasChord: false))
    }

    func testEncodingCaptionAppendsSameByteDisclosureOnlyWhenPresent() {
        let plain = ChordEncoding(bytes: [0x01], summary: "0x01", usesCsiU: false, sameAsPlainKey: nil)
        XCTAssertEqual(ShortcutPickerLogic.encodingCaption(plain), "Sends: 0x01")

        let disclosed = ChordEncoding(bytes: [0x09], summary: "0x09", usesCsiU: false, sameAsPlainKey: "Tab")
        XCTAssertEqual(ShortcutPickerLogic.encodingCaption(disclosed), "Sends: 0x09 — same byte as Tab")
    }

    func testClampNameCapsAtMaxNameLength() {
        let long = String(repeating: "x", count: SavedShortcutUi.maxNameLength + 50)
        XCTAssertEqual(ShortcutPickerLogic.clampName(long), String(repeating: "x", count: 100))
        XCTAssertEqual(ShortcutPickerLogic.clampName("ok"), "ok", "short names pass through untouched")
    }

    func testClampNameCountsCodePointsNotGraphemes() {
        // 🇺🇸 is one grapheme but two code points: 100 flags fit a
        // `String.prefix(100)` clamp yet weigh 200 code points — the server's
        // Pydantic `max_length=100` then 422s. The clamp must count in the
        // server's unit.
        let flag = "\u{1F1FA}\u{1F1F8}"
        let hundredFlags = String(repeating: flag, count: 100)

        let clamped = ShortcutPickerLogic.clampName(hundredFlags)

        XCTAssertEqual(clamped, String(repeating: flag, count: 50))
        XCTAssertEqual(clamped.unicodeScalars.count, SavedShortcutUi.maxNameLength)
    }

    func testClampNameNeverSplitsAGraphemeCluster() {
        // 👨‍👩‍👧‍👦 is seven code points (four people joined by ZWJs); the clamp
        // must stop short of the limit rather than emit a partial cluster.
        let family = "\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}\u{200D}\u{1F466}"

        let clampedFamilies = ShortcutPickerLogic.clampName(String(repeating: family, count: 20))
        XCTAssertEqual(
            clampedFamilies, String(repeating: family, count: 14),
            "floor(100 / 7) whole clusters — never a truncated one"
        )
        XCTAssertEqual(clampedFamilies.unicodeScalars.count, 98)

        let nearLimit = String(repeating: "x", count: 99) + family
        XCTAssertEqual(
            ShortcutPickerLogic.clampName(nearLimit), String(repeating: "x", count: 99),
            "a cluster straddling the limit is dropped whole, not split to hit exactly 100"
        )
    }

    func testClampNameAtExactlyMaxCodePointsPassesThroughUntouched() {
        let flag = "\u{1F1FA}\u{1F1F8}"
        let exactlyAtLimit = String(repeating: flag, count: 50)
        XCTAssertEqual(ShortcutPickerLogic.clampName(exactlyAtLimit), exactlyAtLimit)
    }

    func testSavedShortcutLabelFallsBackToChordDisplay() {
        let chord = KeyChord(ctrl: true, key: "a")
        XCTAssertEqual(SavedShortcutUi(id: "1", chord: chord, name: "Alpha", risky: false).label, "Alpha")
        XCTAssertEqual(SavedShortcutUi(id: "1", chord: chord, name: nil, risky: false).label, "Ctrl+A")
    }
}
