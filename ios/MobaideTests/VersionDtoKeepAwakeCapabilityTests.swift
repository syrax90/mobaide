import XCTest
@testable import Mobaide

/// `VersionInfoResponse.keepAwakeCapability`: only an explicit `true` shows
/// the Keep awake setting, so an agent without `/api/agent/settings` never
/// gets a toggle that would 404.
final class VersionDtoKeepAwakeCapabilityTests: XCTestCase {
    private func version(keepAwake: JSONValue) -> VersionInfoResponse {
        VersionInfoResponse(
            name: "mobaide-agent",
            server_version: "1.4.0",
            protocol_versions: [7],
            min_client_version: 1,
            capabilities: ["agent_settings": .object(["keep_awake": keepAwake])]
        )
    }

    func testReadsKeepAwakeFromAnExplicitTrueOnly() {
        XCTAssertTrue(version(keepAwake: true).keepAwakeCapability)
        XCTAssertFalse(version(keepAwake: false).keepAwakeCapability)
        XCTAssertFalse(version(keepAwake: "true").keepAwakeCapability)
        XCTAssertFalse(version(keepAwake: 1).keepAwakeCapability)
        XCTAssertFalse(version(keepAwake: .null).keepAwakeCapability)
        XCTAssertFalse(version(keepAwake: ["enabled": true]).keepAwakeCapability)
    }

    func testTheVersionFixtureOffersKeepAwakeOnlyInItsAgentSettingsCase() throws {
        let offered = try FixtureLoader.decodeCase(
            VersionInfoResponse.self,
            path: "dto/agent/VersionInfoResponse.json",
            name: "agent_settings_capability"
        )
        let older = try FixtureLoader.decodeCase(
            VersionInfoResponse.self,
            path: "dto/agent/VersionInfoResponse.json",
            name: "voice_capability"
        )

        XCTAssertTrue(offered.keepAwakeCapability)
        XCTAssertFalse(older.keepAwakeCapability)
    }
}
