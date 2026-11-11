import XCTest
@testable import Mobaide

final class JSONValueTests: XCTestCase {
    func testDecodePreservesScalarTypes() throws {
        let json = #"{"s":"x","i":7,"d":1.5,"b":true,"n":null,"a":[1,"two"],"o":{"k":false}}"#
        let value = try WireJSON.decoder().decode(JSONValue.self, from: Data(json.utf8))
        XCTAssertEqual(value, [
            "s": "x",
            "i": 7,
            "d": 1.5,
            "b": true,
            "n": nil,
            "a": [1, "two"],
            "o": ["k": false],
        ])
    }

    func testBoolDoesNotDecodeAsInt() throws {
        let value = try WireJSON.decoder().decode(JSONValue.self, from: Data("[true,1,0,false]".utf8))
        XCTAssertEqual(value, [true, 1, 0, false])
    }
}
