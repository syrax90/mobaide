import XCTest
@testable import Mobaide

final class ProtocolVersionTests: XCTestCase {
    func testProtocolConstantsAreWiredFromXcconfig() {
        XCTAssertEqual(ProtocolVersion.app, 8)
        XCTAssertEqual(ProtocolVersion.minServer, 6)
    }
}
