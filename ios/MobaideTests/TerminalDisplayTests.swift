import XCTest
@testable import Mobaide

final class TerminalDisplayTests: XCTestCase {
    func testDisplayModeStorageCoercesUnknownAndAbsentToStatic() {
        XCTAssertEqual(TerminalDisplayMode(storage: "full"), .full)
        XCTAssertEqual(TerminalDisplayMode(storage: "static"), .static)
        XCTAssertEqual(TerminalDisplayMode(storage: nil), .static, "absent value falls back to the shipped default")
        XCTAssertEqual(TerminalDisplayMode(storage: "landscape"), .static, "unknown raw value falls back to static")
        XCTAssertEqual(TerminalDisplayMode(storage: ""), .static, "empty string is not a valid case")
    }
}
