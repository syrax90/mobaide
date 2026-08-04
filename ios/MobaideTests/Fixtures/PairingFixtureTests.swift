import XCTest
@testable import Mobaide

private struct PairingFixtureFile: Decodable {
    let parse_cases: [ParseCase]

    struct ParseCase: Decodable {
        let name: String
        let raw: String
        let expect: Expectation?
    }

    struct Expectation: Decodable {
        let version: String
        let relay_url: String
        let nonce: String
        let trust_key_fp: String
        let exp_unix: Int64
    }
}

/// The iOS arm of `tests/protocol_fixtures/pairing/qr_payload.json`.
///
/// `PairingPayloadTests` covers the same parser against hand-written cases;
/// this drives the table the agent, Android and the web client all read, so a
/// tightening on one platform cannot land without the others following.
/// Encoding and the pairing link are agent-and-web only — iOS never produces a
/// payload — so `encode_cases` and `link_cases` are deliberately not run here.
final class PairingFixtureTests: XCTestCase {
    private func loadFile() throws -> PairingFixtureFile {
        try FixtureLoader.load(PairingFixtureFile.self, path: "pairing/qr_payload.json")
    }

    func testParseTable() throws {
        let file = try loadFile()
        XCTAssertFalse(file.parse_cases.isEmpty)
        for parseCase in file.parse_cases {
            let parsed = PairingPayload.parseMoba1(parseCase.raw)
            guard let expected = parseCase.expect else {
                XCTAssertNil(parsed, parseCase.name)
                continue
            }
            XCTAssertEqual(
                parsed,
                ParsedQrPairingPayload(
                    version: expected.version,
                    relayUrl: expected.relay_url,
                    nonce: expected.nonce,
                    trustKeyFp: expected.trust_key_fp,
                    expUnix: expected.exp_unix
                ),
                parseCase.name
            )
        }
    }

    /// The golden is only worth having if it carries the rejections; a table
    /// of happy paths would pass against a parser that accepted everything.
    func testTableCoversBothOutcomes() throws {
        let file = try loadFile()
        XCTAssertGreaterThanOrEqual(file.parse_cases.filter { $0.expect != nil }.count, 3)
        XCTAssertGreaterThanOrEqual(file.parse_cases.filter { $0.expect == nil }.count, 10)
    }
}
