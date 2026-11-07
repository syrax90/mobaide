import XCTest
@testable import Mobaide

/// `VersionInfoResponse.voiceCapability`'s `cleanup` flag: only an explicit
/// `true` offers transcript cleanup, and a malformed flag never costs the
/// dictation limits beside it.
final class VersionDtoVoiceCapabilityTests: XCTestCase {
    private func voice(cleanup: JSONValue?) -> VoiceCapability? {
        var flags: [String: JSONValue] = [
            "dictation": true,
            "sample_rate": 16_000,
            "max_seconds": 120,
            "max_chunk_bytes": 65_536,
        ]
        if let cleanup { flags["cleanup"] = cleanup }
        return VersionInfoResponse(
            name: "mobaide-agent",
            server_version: "1.4.0",
            protocol_versions: [7],
            min_client_version: 1,
            capabilities: ["voice": .object(flags)]
        ).voiceCapability
    }

    func testReadsCleanupFromAnExplicitTrueOnly() {
        let without = VoiceCapability(sampleRate: 16_000, maxSeconds: 120, maxChunkBytes: 65_536)
        let with = VoiceCapability(sampleRate: 16_000, maxSeconds: 120, maxChunkBytes: 65_536, cleanup: true)

        XCTAssertEqual(voice(cleanup: true), with)
        XCTAssertEqual(voice(cleanup: nil), without)
        XCTAssertEqual(voice(cleanup: false), without)
        XCTAssertEqual(voice(cleanup: "true"), without)
        XCTAssertEqual(voice(cleanup: 1), without)
        XCTAssertEqual(voice(cleanup: .null), without)
        XCTAssertEqual(voice(cleanup: ["enabled": true]), without)
    }

    func testTheVersionFixtureOffersCleanup() throws {
        let version = try FixtureLoader.decodeCase(
            VersionInfoResponse.self,
            path: "dto/agent/VersionInfoResponse.json",
            name: "voice_capability"
        )

        XCTAssertEqual(
            version.voiceCapability,
            VoiceCapability(sampleRate: 16_000, maxSeconds: 120, maxChunkBytes: 65_536, cleanup: true)
        )
    }
}
