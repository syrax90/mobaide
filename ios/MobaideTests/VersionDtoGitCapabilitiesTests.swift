import XCTest
@testable import Mobaide

/// `VersionInfoResponse.gitCapability(_:)` and `gitCapabilities`: the
/// `capabilities.git` flags every mutating control of the Changes screen
/// hangs off. The opposite default from `cliAvailable` — only an explicit
/// `true` counts, so an older agent keeps the read-only viewer.
final class VersionDtoGitCapabilitiesTests: XCTestCase {
    private func info(_ capabilities: JSONValue) -> VersionInfoResponse {
        VersionInfoResponse(
            name: "mobaide-agent",
            server_version: "1.4.0",
            protocol_versions: [7],
            min_client_version: 1,
            capabilities: capabilities
        )
    }

    func testReadsAnExplicitTrueAndNothingElse() {
        let probe = info(["git": ["changes": true, "stage": true, "discard": "true", "commit": false]])

        XCTAssertTrue(probe.gitCapability("changes"))
        XCTAssertTrue(probe.gitCapability("stage"))
        XCTAssertFalse(probe.gitCapability("discard"), "unlike cliAvailable, the stringified form is not enough")
        XCTAssertFalse(probe.gitCapability("commit"))
    }

    func testAbsentOrUnreadableReadsAsFalse() {
        XCTAssertFalse(info([:]).gitCapability("stage"))
        XCTAssertFalse(info(["git": "yes"]).gitCapability("stage"))
        XCTAssertFalse(info(["git": ["changes": true]]).gitCapability("stage"))
        XCTAssertFalse(info(["git": ["stage": nil]]).gitCapability("stage"))
        XCTAssertFalse(info(["git": ["stage": 1]]).gitCapability("stage"))
    }

    func testGitCapabilitiesCollectsTheThreeMutatingFlags() {
        XCTAssertEqual(
            info(["git": ["changes": true, "stage": true, "commit": true]]).gitCapabilities,
            GitCapabilities(stage: true, discard: false, commit: true)
        )
        XCTAssertEqual(info([:]).gitCapabilities, .none)
        XCTAssertEqual(GitCapabilities.none, GitCapabilities(stage: false, discard: false, commit: false))
    }

    func testADecodedProbeReadsTheSame() throws {
        let wire = Data(#"""
        {"name":"mobaide-agent","server_version":"1.4.0","protocol_versions":[7],"min_client_version":1,
         "capabilities":{"git":{"changes":true,"stage":true,"discard":true,"commit":true}}}
        """#.utf8)

        let probe = try WireJSON.decoder().decode(VersionInfoResponse.self, from: wire)

        XCTAssertEqual(probe.gitCapabilities, GitCapabilities(stage: true, discard: true, commit: true))
    }
}
