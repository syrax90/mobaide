import XCTest
@testable import Mobaide

/// `VersionInfoResponse.voiceCapability`'s `cleanup` flag and `cleanup_kinds`:
/// only an explicit `true` offers transcript cleanup, an agent without a
/// usable kind list cleans up for Claude Code only, and a malformed key never
/// costs the dictation limits beside it.
final class VersionDtoVoiceCapabilityTests: XCTestCase {
    private func voice(cleanup: JSONValue?, kinds: JSONValue? = nil) -> VoiceCapability? {
        var flags: [String: JSONValue] = [
            "dictation": true,
            "sample_rate": 16_000,
            "max_seconds": 120,
            "max_chunk_bytes": 65_536,
        ]
        if let cleanup { flags["cleanup"] = cleanup }
        if let kinds { flags["cleanup_kinds"] = kinds }
        return VersionInfoResponse(
            name: "mobaide-agent",
            server_version: "1.4.0",
            protocol_versions: [7],
            min_client_version: 1,
            capabilities: ["voice": .object(flags)]
        ).voiceCapability
    }

    private func capability(cleanup: Bool = true, kinds: [String]) -> VoiceCapability {
        VoiceCapability(
            sampleRate: 16_000, maxSeconds: 120, maxChunkBytes: 65_536, cleanup: cleanup, cleanupKinds: kinds
        )
    }

    func testReadsCleanupFromAnExplicitTrueOnly() {
        let without = VoiceCapability(sampleRate: 16_000, maxSeconds: 120, maxChunkBytes: 65_536)

        XCTAssertEqual(voice(cleanup: true), capability(kinds: ["claude"]))
        XCTAssertEqual(voice(cleanup: nil), without)
        XCTAssertEqual(voice(cleanup: false), without)
        XCTAssertEqual(voice(cleanup: "true"), without)
        XCTAssertEqual(voice(cleanup: 1), without)
        XCTAssertEqual(voice(cleanup: .null), without)
        XCTAssertEqual(voice(cleanup: ["enabled": true]), without)
    }

    func testCleanupKindsAreEmptyUnlessCleanupIsTrue() {
        XCTAssertEqual(voice(cleanup: nil, kinds: ["claude", "codex"])?.cleanupKinds, [])
        XCTAssertEqual(voice(cleanup: false, kinds: ["claude", "codex"])?.cleanupKinds, [])
    }

    func testAnAgentWithoutAKindListCleansUpForClaudeOnly() {
        XCTAssertEqual(voice(cleanup: true)?.cleanupKinds, ["claude"])
        XCTAssertEqual(voice(cleanup: true, kinds: "codex")?.cleanupKinds, ["claude"])
        XCTAssertEqual(voice(cleanup: true, kinds: ["codex": true])?.cleanupKinds, ["claude"])
        XCTAssertEqual(voice(cleanup: true, kinds: .null)?.cleanupKinds, ["claude"])
    }

    func testCleanupIsOfferedForTheListedSessionKindsOnly() {
        let all = capability(kinds: ["claude", "codex", "antigravity"])

        XCTAssertTrue(all.cleansUp(for: .claude))
        XCTAssertTrue(all.cleansUp(for: .codex))
        XCTAssertTrue(all.cleansUp(for: .antigravity))
        XCTAssertFalse(all.cleansUp(for: .shell))
        XCTAssertFalse(all.cleansUp(for: .autoScan))
        XCTAssertFalse(all.cleansUp(for: .custom("custom_ab12")))
        XCTAssertFalse(all.cleansUp(for: nil))
    }

    func testTheVersionFixtureOffersCleanupForEveryAgentCli() throws {
        let version = try FixtureLoader.decodeCase(
            VersionInfoResponse.self,
            path: "dto/agent/VersionInfoResponse.json",
            name: "voice_capability"
        )

        XCTAssertEqual(
            version.voiceCapability,
            capability(kinds: ["claude", "codex", "antigravity", "opencode", "copilot"])
        )
        XCTAssertEqual(version.voiceCapability?.cleansUp(for: .opencode), true)
    }
}
