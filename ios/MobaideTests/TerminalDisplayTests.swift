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

    func testClampColumnsConstrainsToInclusiveRange() {
        XCTAssertEqual(TerminalDisplayDefaults.clampColumns(59), 60, "just below the floor snaps up")
        XCTAssertEqual(TerminalDisplayDefaults.clampColumns(10), 60, "far below the floor snaps up")
        XCTAssertEqual(TerminalDisplayDefaults.clampColumns(241), 240, "just above the ceiling snaps down")
        XCTAssertEqual(TerminalDisplayDefaults.clampColumns(5000), 240, "far above the ceiling snaps down")
        XCTAssertEqual(TerminalDisplayDefaults.clampColumns(60), 60, "floor is inclusive")
        XCTAssertEqual(TerminalDisplayDefaults.clampColumns(240), 240, "ceiling is inclusive")
        XCTAssertEqual(TerminalDisplayDefaults.clampColumns(120), 120, "in-range value is unchanged")
    }
}
