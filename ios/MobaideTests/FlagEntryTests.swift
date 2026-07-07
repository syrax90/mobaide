import XCTest
@testable import Mobaide

/// The `default_flags` editor round-trip: `FlagEntry.from(key:value:)` seeds a
/// row from a wire value and `toJSONValue()` re-encodes it. The two must be
/// symmetric so an untouched edit doesn't silently retype a flag.
final class FlagEntryTests: XCTestCase {
    // MARK: - toJSONValue()

    func testStringValueEncodesAsStringEvenWhenItReadsTrue() {
        let entry = FlagEntry(key: "k", kind: .string, textValue: "true")
        XCTAssertEqual(entry.toJSONValue(), .string("true"))
    }

    func testBooleanValueEncodesAsBool() {
        XCTAssertEqual(FlagEntry(key: "k", kind: .boolean, textValue: "true").toJSONValue(), .bool(true))
        XCTAssertEqual(FlagEntry(key: "k", kind: .boolean, textValue: "false").toJSONValue(), .bool(false))
    }

    func testNumberPrefersIntThenDouble() {
        XCTAssertEqual(FlagEntry(key: "k", kind: .number, textValue: "5").toJSONValue(), .int(5))
        XCTAssertEqual(FlagEntry(key: "k", kind: .number, textValue: "-3").toJSONValue(), .int(-3))
        XCTAssertEqual(FlagEntry(key: "k", kind: .number, textValue: "5.0").toJSONValue(), .double(5.0))
        XCTAssertEqual(FlagEntry(key: "k", kind: .number, textValue: "5.5").toJSONValue(), .double(5.5))
    }

    func testUnparseableNumberDefaultsToZero() {
        XCTAssertEqual(FlagEntry(key: "k", kind: .number, textValue: "abc").toJSONValue(), .double(0))
    }

    // MARK: - from(key:value:)

    func testFromStringKeepsStringKindEvenForTrueText() {
        let entry = FlagEntry.from(key: "k", value: .string("true"))
        XCTAssertEqual(entry.kind, .string)
        XCTAssertEqual(entry.textValue, "true")
    }

    func testFromBoolMapsToBooleanKind() {
        XCTAssertEqual(FlagEntry.from(key: "k", value: .bool(true)).kind, .boolean)
        XCTAssertEqual(FlagEntry.from(key: "k", value: .bool(true)).textValue, "true")
        XCTAssertEqual(FlagEntry.from(key: "k", value: .bool(false)).textValue, "false")
    }

    func testFromIntMapsToNumberKind() {
        let entry = FlagEntry.from(key: "k", value: .int(42))
        XCTAssertEqual(entry.kind, .number)
        XCTAssertEqual(entry.textValue, "42")
    }

    func testFromDoubleRendersWholeNumbersWithoutDecimal() {
        XCTAssertEqual(FlagEntry.from(key: "k", value: .double(1.0)).textValue, "1")
        XCTAssertEqual(FlagEntry.from(key: "k", value: .double(1.5)).textValue, "1.5")
    }

    func testFromUnsupportedValueFallsBackToStringKind() {
        // Arrays/objects/null have no dedicated editor control → edited as text.
        XCTAssertEqual(FlagEntry.from(key: "k", value: .null).kind, .string)
        XCTAssertEqual(FlagEntry.from(key: "k", value: .array([.int(1)])).kind, .string)
    }

    // MARK: - Round-trips (from → toJSONValue)

    func testStringTrueRoundTripsWithoutBecomingBool() {
        let value = JSONValue.string("true")
        XCTAssertEqual(FlagEntry.from(key: "k", value: value).toJSONValue(), value)
    }

    func testBoolRoundTrips() {
        XCTAssertEqual(FlagEntry.from(key: "k", value: .bool(true)).toJSONValue(), .bool(true))
    }

    func testIntRoundTrips() {
        XCTAssertEqual(FlagEntry.from(key: "k", value: .int(7)).toJSONValue(), .int(7))
    }

    func testFractionalDoubleRoundTrips() {
        XCTAssertEqual(FlagEntry.from(key: "k", value: .double(1.5)).toJSONValue(), .double(1.5))
    }

    func testWholeDoubleCanonicalizesToInt() {
        // 2.0 seeds textValue "2" (whole), which re-encodes as .int(2) — the
        // documented whole-number canonicalization, not a fractional double.
        XCTAssertEqual(FlagEntry.from(key: "k", value: .double(2.0)).toJSONValue(), .int(2))
    }
}
