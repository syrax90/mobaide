import XCTest
@testable import Mobaide

final class JWTTests: XCTestCase {
    func testExpirationParse() throws {
        XCTAssertEqual(try JWT.expirationSeconds(of: TestJWT.token(exp: 1_234_567_890)), 1_234_567_890)
        XCTAssertNil(try JWT.expirationSeconds(of: TestJWT.token(exp: nil)))
    }

    func testMalformedTokensThrow() {
        XCTAssertThrowsError(try JWT.expirationSeconds(of: ""))
        XCTAssertThrowsError(try JWT.expirationSeconds(of: "onesegment"))
        XCTAssertThrowsError(try JWT.expirationSeconds(of: "a.!!!.c"))
        XCTAssertThrowsError(try JWT.expirationSeconds(of: "a..c"), "empty payload segment")
        let nonObjectPayload = "h.\(TestJWT.base64URL("[1,2]")).s"
        XCTAssertThrowsError(try JWT.expirationSeconds(of: nonObjectPayload))
    }

    func testBase64URLDecodingHandlesUnpaddedUrlSafeAlphabet() {
        // "_-__" is base64url for classic "/+//" = bytes ff ef ff.
        XCTAssertEqual(JWT.decodeBase64URL("_-__"), Data([0xFF, 0xEF, 0xFF]))
        XCTAssertEqual(JWT.decodeBase64URL(TestJWT.base64URL("{}")), Data("{}".utf8))
    }
}
