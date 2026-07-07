import XCTest
@testable import Mobaide

final class EmailFormattingTests: XCTestCase {
    // Android EmailFormatting.kt behavioral parity. (The Kotlin KDoc example
    // for "alice" shows four bullets, but the code emits length-2 = 3 — the
    // code is the source of truth.)
    func testMaskEmail() {
        XCTAssertEqual(EmailFormatting.maskEmail("alice@example.com"), "a•••e@example.com")
        XCTAssertEqual(EmailFormatting.maskEmail("a@example.com"), "a•••@example.com")
        XCTAssertEqual(EmailFormatting.maskEmail("ab@example.com"), "a•@example.com")
        XCTAssertEqual(EmailFormatting.maskEmail("abc@example.com"), "a••@example.com")
        XCTAssertEqual(EmailFormatting.maskEmail(""), "")
    }

    func testMalformedInputsPassThrough() {
        XCTAssertEqual(EmailFormatting.maskEmail("@example.com"), "@example.com")
        XCTAssertEqual(EmailFormatting.maskEmail("no-at-sign"), "no-at-sign")
    }
}
