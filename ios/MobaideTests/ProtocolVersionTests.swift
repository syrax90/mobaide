import XCTest
@testable import Mobaide

final class ProtocolVersionTests: XCTestCase {
    func testProtocolConstantsAreWiredFromXcconfig() {
        XCTAssertEqual(ProtocolVersion.app, 6)
        XCTAssertEqual(ProtocolVersion.minServer, 6)
    }
}
