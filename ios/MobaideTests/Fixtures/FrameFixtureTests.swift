import XCTest
@testable import Mobaide

private struct FrameFixtureFile: Decodable {
    let encode: [EncodeCase]
    let decode: [DecodeCase]

    struct EncodeCase: Decodable {
        let name: String
        let frame: FrameSpec
        let bytes_hex: String
    }

    struct FrameSpec: Decodable {
        let kind: String
        let rows: Int?
        let cols: Int?
        let text: String?
    }

    struct DecodeCase: Decodable {
        let name: String
        let bytes_hex: String
        let expect: ExpectSpec?
    }

    struct ExpectSpec: Decodable {
        let kind: String
        let payload_hex: String?
        let status: String?
        let pid: Int?
    }
}

final class FrameFixtureTests: XCTestCase {
    private func loadFile() throws -> FrameFixtureFile {
        try FixtureLoader.load(FrameFixtureFile.self, path: "frames/terminal_frames.json")
    }

    func testEncodeCasesMatchGoldenBytes() throws {
        let file = try loadFile()
        XCTAssertFalse(file.encode.isEmpty)
        for encodeCase in file.encode {
            let expected = try XCTUnwrap(Data(hexString: encodeCase.bytes_hex), encodeCase.name)
            let frame = try frame(from: encodeCase.frame, caseName: encodeCase.name)
            XCTAssertEqual(frame.encoded.hexString, expected.hexString, encodeCase.name)
        }
    }

    func testDecodeCasesMatchExpectations() throws {
        let file = try loadFile()
        XCTAssertFalse(file.decode.isEmpty)
        for decodeCase in file.decode {
            let bytes = try XCTUnwrap(Data(hexString: decodeCase.bytes_hex), decodeCase.name)
            let decoded = TerminalInboundFrame.decode(bytes)
            guard let expect = decodeCase.expect else {
                XCTAssertNil(decoded, "\(decodeCase.name): expected nil for \(decodeCase.bytes_hex)")
                continue
            }
            switch (expect.kind, decoded) {
            case ("output", .output(let payload)):
                XCTAssertEqual(payload.hexString, expect.payload_hex ?? "", decodeCase.name)
            case ("state", .state(let json)):
                let tree = try WireJSON.decoder().decode(JSONValue.self, from: Data(json.utf8))
                var expected: [String: JSONValue] = [:]
                if let status = expect.status { expected["status"] = .string(status) }
                if let pid = expect.pid { expected["pid"] = .int(pid) }
                XCTAssertEqual(tree, .object(expected), decodeCase.name)
            case ("replay_start", .replayStart), ("replay_end", .replayEnd):
                break
            default:
                XCTFail("\(decodeCase.name): expected \(expect.kind), got \(String(describing: decoded))")
            }
        }
    }

    private func frame(from spec: FrameFixtureFile.FrameSpec, caseName: String) throws -> TerminalOutboundFrame {
        switch spec.kind {
        case "resize":
            let rows = try XCTUnwrap(spec.rows, caseName)
            let cols = try XCTUnwrap(spec.cols, caseName)
            return .resize(rows: UInt16(rows), cols: UInt16(cols))
        case "input":
            return .input(try XCTUnwrap(spec.text, caseName))
        case "execute":
            return .execute(try XCTUnwrap(spec.text, caseName))
        case "submit":
            return .submit(try XCTUnwrap(spec.text, caseName))
        default:
            throw FixtureLoaderError.fileNotFound("unknown frame kind \(spec.kind) in \(caseName)")
        }
    }
}
