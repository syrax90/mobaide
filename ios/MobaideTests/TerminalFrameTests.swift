import XCTest
@testable import Mobaide

final class TerminalFrameTests: XCTestCase {
    func testResizeGolden40x120() {
        let encoded = TerminalOutboundFrame.resize(rows: 40, cols: 120).encoded
        XCTAssertEqual(encoded, Data([0x72, 0x00, 0x28, 0x00, 0x78]))
    }

    func testResizeBigEndianRowsFirst() {
        let encoded = TerminalOutboundFrame.resize(rows: 0x0102, cols: 0x0304).encoded
        XCTAssertEqual(encoded, Data([0x72, 0x01, 0x02, 0x03, 0x04]))
    }

    func testOutboundTypeBytes() {
        XCTAssertEqual(TerminalOutboundFrame.input("a").encoded.first, 0x69)
        XCTAssertEqual(TerminalOutboundFrame.execute("a").encoded.first, 0x78)
        XCTAssertEqual(TerminalOutboundFrame.submit("a").encoded.first, 0x6D)
    }

    func testInputPayloadIsUTF8AtOffsetOne() {
        let encoded = TerminalOutboundFrame.input("hé").encoded
        XCTAssertEqual(encoded, Data([0x69]) + Data("hé".utf8))
    }

    func testEmptyPayloadEncodesTypeByteOnly() {
        XCTAssertEqual(TerminalOutboundFrame.submit("").encoded, Data([0x6D]))
    }

    func testDecodeOutput() {
        let frame = Data([0x6F, 0x68, 0x69])
        XCTAssertEqual(TerminalInboundFrame.decode(frame), .output(Data([0x68, 0x69])))
    }

    func testDecodeStateKeepsRawJSON() {
        let json = #"{"status":"running","pid":42}"#
        let frame = Data([0x73]) + Data(json.utf8)
        XCTAssertEqual(TerminalInboundFrame.decode(frame), .state(json: json))
    }

    func testDecodeReplayMarkers() {
        XCTAssertEqual(TerminalInboundFrame.decode(Data([0x62])), .replayStart)
        XCTAssertEqual(TerminalInboundFrame.decode(Data([0x65])), .replayEnd)
    }

    func testDecodeEmptyFrameIsNil() {
        XCTAssertNil(TerminalInboundFrame.decode(Data()))
    }

    func testDecodeUnknownTypeByteIsNil() {
        XCTAssertNil(TerminalInboundFrame.decode(Data([0x7A, 0x00])))
    }

    func testDecodeOutboundOnlyTypeBytesAreNil() {
        XCTAssertNil(TerminalInboundFrame.decode(Data([0x69, 0x68])))
        XCTAssertNil(TerminalInboundFrame.decode(Data([0x78, 0x68])))
        XCTAssertNil(TerminalInboundFrame.decode(Data([0x6D, 0x68])))
        XCTAssertNil(TerminalInboundFrame.decode(Data([0x72, 0x00, 0x28, 0x00, 0x78])))
    }
}
