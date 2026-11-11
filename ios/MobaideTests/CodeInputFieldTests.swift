import XCTest
@testable import Mobaide

/// `CodeInputField.sanitized` is the single normalizer behind both typed
/// input and the keyboard `PasteButton`, so a sloppy paste has to survive it.
final class CodeInputFieldTests: XCTestCase {
    private func sanitized(_ raw: String) -> String {
        CodeInputField.sanitized(raw, length: 6)
    }

    func testStripsNonDigitsSoPastedEmailTextStillWorks() {
        XCTAssertEqual(sanitized("Your Mobaide code is 123 456"), "123456")
        XCTAssertEqual(sanitized("12-34-56\n"), "123456")
    }

    // The server accepts ASCII 0-9 only; Character.isNumber alone would let
    // Arabic-Indic digits and fractions through.
    func testRejectsNonASCIINumerics() {
        XCTAssertEqual(sanitized("١٢٣٤٥٦"), "")
        XCTAssertEqual(sanitized("½"), "")
        XCTAssertEqual(sanitized("1٢3"), "13")
    }
}
