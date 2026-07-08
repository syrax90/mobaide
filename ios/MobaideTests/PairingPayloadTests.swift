import XCTest
@testable import Mobaide

final class PairingPayloadTests: XCTestCase {
    func testValidPayload() {
        let parsed = PairingPayload.parseMoba1("MOBA1|https://relay.example.com|nonce-123|fp-abc|1893456000")
        XCTAssertEqual(
            parsed,
            ParsedQrPairingPayload(
                version: "MOBA1",
                relayUrl: "https://relay.example.com",
                nonce: "nonce-123",
                trustKeyFp: "fp-abc",
                expUnix: 1_893_456_000
            )
        )
    }

    func testNegativeExpStillParses() {
        // Parsing does not range-check expiry (that lives in the state machine).
        XCTAssertEqual(PairingPayload.parseMoba1("MOBA1|r|n|f|-5")?.expUnix, -5)
    }

    func testWrongFieldCountIsMalformed() {
        XCTAssertNil(PairingPayload.parseMoba1("MOBA1|r|n|f"))        // 4 fields
        XCTAssertNil(PairingPayload.parseMoba1("MOBA1|r|n|f|100|x"))  // 6 fields
        XCTAssertNil(PairingPayload.parseMoba1(""))                   // 0/1 fields
    }

    func testUnknownVersionIsMalformed() {
        XCTAssertNil(PairingPayload.parseMoba1("MOBA2|r|n|f|100"))
        XCTAssertNil(PairingPayload.parseMoba1("moba1|r|n|f|100"))
    }

    func testBlankRequiredFieldsAreMalformed() {
        XCTAssertNil(PairingPayload.parseMoba1("MOBA1||n|f|100"))   // blank relay
        XCTAssertNil(PairingPayload.parseMoba1("MOBA1|r||f|100"))   // blank nonce
        XCTAssertNil(PairingPayload.parseMoba1("MOBA1|r|n| |100"))  // whitespace-only fp
    }

    func testNonIntegerExpiryIsMalformed() {
        XCTAssertNil(PairingPayload.parseMoba1("MOBA1|r|n|f|not-a-number"))
        XCTAssertNil(PairingPayload.parseMoba1("MOBA1|r|n|f|"))  // empty exp
    }
}
