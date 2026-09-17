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

/// The three job keys and `hasSync`, which gates the menu entry and
/// the Sync button on any of them.
final class VersionDtoGitSyncCapabilitiesTests: XCTestCase {
    private func info(_ capabilities: JSONValue) -> VersionInfoResponse {
        VersionInfoResponse(
            name: "mobaide-agent",
            server_version: "1.4.0",
            protocol_versions: [7],
            min_client_version: 1,
            capabilities: capabilities
        )
    }

    func testReadsEachJobKeyAsAnExplicitTrue() {
        let probe = info(["git": ["changes": true, "fetch": true, "pull": "true", "push": false]])

        XCTAssertEqual(
            probe.gitCapabilities,
            GitCapabilities(stage: false, discard: false, commit: false, fetch: true, pull: false, push: false)
        )
    }

    func testHasSyncOnAnyJobKeyAndNotForAStageAndCommitAgent() {
        XCTAssertTrue(info(["git": ["push": true]]).gitCapabilities.hasSync)
        XCTAssertTrue(info(["git": ["pull": true]]).gitCapabilities.hasSync)
        XCTAssertTrue(info(["git": ["fetch": true]]).gitCapabilities.hasSync)
        XCTAssertFalse(
            info(["git": ["changes": true, "stage": true, "discard": true, "commit": true]]).gitCapabilities.hasSync
        )
        XCTAssertFalse(GitCapabilities.none.hasSync)
        XCTAssertEqual(
            GitCapabilities.none,
            GitCapabilities(stage: false, discard: false, commit: false, fetch: false, pull: false, push: false)
        )
    }

    func testADecodedProbeReadsTheSixKeys() throws {
        let wire = Data(#"""
        {"name":"mobaide-agent","server_version":"1.4.0","protocol_versions":[7],"min_client_version":1,
         "capabilities":{"git":{"changes":true,"stage":true,"discard":true,"commit":true,"fetch":true,"pull":true,"push":true}}}
        """#.utf8)

        let probe = try WireJSON.decoder().decode(VersionInfoResponse.self, from: wire)

        XCTAssertEqual(
            probe.gitCapabilities,
            GitCapabilities(stage: true, discard: true, commit: true, fetch: true, pull: true, push: true)
        )
        XCTAssertTrue(probe.gitCapabilities.hasSync)
    }
}

/// `rebase` (the Rebase… entry and the banner's Continue / Skip / Abort) and
/// `resolve` (Mark resolved on a conflict row), each read on its own.
final class VersionDtoGitRebaseCapabilitiesTests: XCTestCase {
    private func info(_ capabilities: JSONValue) -> VersionInfoResponse {
        VersionInfoResponse(
            name: "mobaide-agent",
            server_version: "1.4.0",
            protocol_versions: [7],
            min_client_version: 1,
            capabilities: capabilities
        )
    }

    func testReadsRebaseAndResolveEachAsAnExplicitTrue() {
        XCTAssertEqual(
            info(["git": ["changes": true, "stage": true, "rebase": true, "resolve": "true"]]).gitCapabilities,
            GitCapabilities(stage: true, rebase: true, resolve: false)
        )
        XCTAssertEqual(
            info(["git": ["changes": true, "stage": true, "resolve": true]]).gitCapabilities,
            GitCapabilities(stage: true, rebase: false, resolve: true)
        )
        XCTAssertFalse(GitCapabilities.none.rebase)
        XCTAssertFalse(GitCapabilities.none.resolve)
    }

    func testADecodedProbeFromTheCurrentAgentReadsBoth() throws {
        let wire = Data(#"""
        {"name":"mobaide-agent","server_version":"1.4.0","protocol_versions":[7],"min_client_version":1,
         "capabilities":{"git":{"changes":true,"stage":true,"discard":true,"commit":true,"fetch":true,"pull":true,"push":true,"branches":true,"rebase":true,"resolve":true}}}
        """#.utf8)

        let probe = try WireJSON.decoder().decode(VersionInfoResponse.self, from: wire)

        XCTAssertTrue(probe.gitCapabilities.rebase)
        XCTAssertTrue(probe.gitCapabilities.resolve)
    }
}
