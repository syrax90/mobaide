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

    func testRoundTripEqualityIsKeyOrderInsensitive() throws {
        let a = try WireJSON.decoder().decode(JSONValue.self, from: Data(#"{"x":1,"y":[{"a":1,"b":2}]}"#.utf8))
        let b = try WireJSON.decoder().decode(JSONValue.self, from: Data(#"{"y":[{"b":2,"a":1}],"x":1}"#.utf8))
        XCTAssertEqual(a, b)
    }

    func testNullIsDistinctFromAbsent() throws {
        let withNull = try WireJSON.decoder().decode(JSONValue.self, from: Data(#"{"y":null}"#.utf8))
        let empty = try WireJSON.decoder().decode(JSONValue.self, from: Data("{}".utf8))
        XCTAssertNotEqual(withNull, empty)
    }

    func testEncodeDecodeRoundTrip() throws {
        let original: JSONValue = ["flags": ["a": 1, "b": ["x", true, nil]], "v": 2.25]
        let data = try WireJSON.encoder().encode(original)
        let decoded = try WireJSON.decoder().decode(JSONValue.self, from: data)
        XCTAssertEqual(decoded, original)
    }

    func testAccessors() {
        let value: JSONValue = ["cli": ["claude": ["available": true, "models": ["a", "b"]]]]
        XCTAssertEqual(value["cli"]?["claude"]?["available"]?.boolValue, true)
        XCTAssertEqual(value["cli"]?["claude"]?["models"]?.arrayValue?.count, 2)
        XCTAssertNil(value["cli"]?["missing"])
    }
}
