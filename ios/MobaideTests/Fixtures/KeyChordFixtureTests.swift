import XCTest
@testable import Mobaide

private struct KeyChordFixtureFile: Decodable {
    let encode: [EncodeCase]
    let encode_rejected: [RejectedEncodeCase]
    let parse_cases: [ParseCase]
    let parse_rejected: [RejectedParseCase]

    struct ChordSpec: Decodable {
        let ctrl: Bool
        let alt: Bool
        let shift: Bool
        let key: String

        var chord: KeyChord { KeyChord(ctrl: ctrl, alt: alt, shift: shift, key: key) }
    }

    struct EncodeCase: Decodable {
        let name: String
        let chord: ChordSpec
        let token: String
        let display: String
        let bytes_hex: String
        let summary: String
        let uses_csi_u: Bool
        let same_as_plain_key: String?
    }

    struct RejectedEncodeCase: Decodable {
        let name: String
        let chord: ChordSpec
    }

    struct ParseCase: Decodable {
        let name: String
        let token: String
        let expect: ChordSpec
        let canonical_token: String
    }

    struct RejectedParseCase: Decodable {
        let name: String
        let token: String
    }
}

/// The iOS arm of `tests/protocol_fixtures/keys/key_chords.json` — the shared
/// chord contract Android, iOS and the web client all encode against.
/// Android's `KeyChordEncoder.kt` remains the reference implementation, so a
/// disagreement between this suite and the fixture means the fixture is wrong.
final class KeyChordFixtureTests: XCTestCase {
    private func loadFile() throws -> KeyChordFixtureFile {
        try FixtureLoader.load(KeyChordFixtureFile.self, path: "keys/key_chords.json")
    }

    func testFixtureHasCasesToRun() throws {
        let file = try loadFile()
        XCTAssertFalse(file.encode.isEmpty)
        XCTAssertFalse(file.encode_rejected.isEmpty)
        XCTAssertFalse(file.parse_cases.isEmpty)
        XCTAssertFalse(file.parse_rejected.isEmpty)
    }

    func testEncodeMatchesTheGoldenBytes() throws {
        let file = try loadFile()
        for encodeCase in file.encode {
            let encoding = try XCTUnwrap(
                KeyChordEncoder.encode(encodeCase.chord.chord),
                "\(encodeCase.name) must encode"
            )
            XCTAssertEqual(
                Data(encoding.bytes).hexString, encodeCase.bytes_hex, "\(encodeCase.name) bytes"
            )
            XCTAssertEqual(encoding.summary, encodeCase.summary, "\(encodeCase.name) summary")
            XCTAssertEqual(encoding.usesCsiU, encodeCase.uses_csi_u, "\(encodeCase.name) usesCsiU")
            XCTAssertEqual(
                encoding.sameAsPlainKey,
                encodeCase.same_as_plain_key,
                "\(encodeCase.name) sameAsPlainKey"
            )
        }
    }

    func testTokenAndDisplayMatchTheGoldenStrings() throws {
        let file = try loadFile()
        for encodeCase in file.encode {
            XCTAssertEqual(
                encodeCase.chord.chord.token, encodeCase.token, "\(encodeCase.name) token"
            )
            XCTAssertEqual(
                encodeCase.chord.chord.display, encodeCase.display, "\(encodeCase.name) display"
            )
        }
    }

    /// The token is what the server stores, so it must reach the same bytes.
    func testEncodingAPersistedTokenReachesTheSameBytes() throws {
        let file = try loadFile()
        for encodeCase in file.encode {
            let parsed = try XCTUnwrap(
                KeyChord.parse(encodeCase.token), "\(encodeCase.name) token must parse"
            )
            let encoding = try XCTUnwrap(
                KeyChordEncoder.encode(parsed), "\(encodeCase.name) must encode from its token"
            )
            XCTAssertEqual(
                Data(encoding.bytes).hexString, encodeCase.bytes_hex, "\(encodeCase.name) bytes"
            )
        }
    }

    func testEncodeRejectsTheGoldenRejections() throws {
        let file = try loadFile()
        for rejected in file.encode_rejected {
            XCTAssertNil(
                KeyChordEncoder.encode(rejected.chord.chord), "\(rejected.name) must not encode"
            )
        }
    }

    func testParseMatchesTheGoldenChordsAndRenormalizes() throws {
        let file = try loadFile()
        for parseCase in file.parse_cases {
            let parsed = KeyChord.parse(parseCase.token)
            XCTAssertEqual(parsed, parseCase.expect.chord, "\(parseCase.name) parse")
            XCTAssertEqual(
                parsed?.token, parseCase.canonical_token, "\(parseCase.name) canonical token"
            )
        }
    }

    func testParseRejectsTheGoldenRejections() throws {
        let file = try loadFile()
        for rejected in file.parse_rejected {
            XCTAssertNil(KeyChord.parse(rejected.token), "\(rejected.name) must not parse")
        }
    }
}
