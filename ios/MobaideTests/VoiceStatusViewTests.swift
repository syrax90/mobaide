import XCTest
@testable import Mobaide

/// The status → mic mapping and the copy the preparing states show.
final class VoiceStatusViewTests: XCTestCase {
    private let capability = VoiceCapability(sampleRate: 16_000, maxSeconds: 120, maxChunkBytes: 65_536)

    private func status(
        _ state: String = VoiceStatusResponse.stateReady,
        repairing: Bool = false,
        progress: VoiceProgress? = nil
    ) -> VoiceStatusResponse {
        VoiceStatusResponse(
            state: state,
            enabled: true,
            repairing: repairing,
            model: VoiceModelInfo(
                id: "parakeet",
                name: "Parakeet",
                languages: ["en"],
                download_bytes: 670_000_000,
                disk_bytes: 700_000_000,
                ram_bytes: 1_200_000_000,
                notice: "CC-BY-4.0"
            ),
            progress: progress,
            host: VoiceHostInfo(
                ram_total_bytes: nil,
                disk_free_bytes: nil,
                cpu_count: nil,
                recommendation: "ok",
                reasons: []
            )
        )
    }

    func testCleanupChoiceIsOffWhenTheAgentOmitsIt() throws {
        var wire = try JSONSerialization.jsonObject(with: JSONEncoder().encode(status())) as! [String: Any]
        wire.removeValue(forKey: "cleanup_enabled")
        let decoded = try JSONDecoder().decode(
            VoiceStatusResponse.self, from: JSONSerialization.data(withJSONObject: wire)
        )

        XCTAssertFalse(decoded.cleanup_enabled)
    }

    func testMicModeMapsEveryKnownStateAndHidesTheRest() {
        let expected: [(String, VoiceMicMode)] = [
            (VoiceStatusResponse.stateNotInstalled, .off),
            (VoiceStatusResponse.stateQueued, .preparing),
            (VoiceStatusResponse.stateDownloading, .preparing),
            (VoiceStatusResponse.stateVerifying, .preparing),
            (VoiceStatusResponse.stateFailed, .failed),
            (VoiceStatusResponse.stateReady, .ready),
            (VoiceStatusResponse.stateUnsupported, .hidden),
            ("some_future_state", .hidden),
        ]
        for (state, mode) in expected {
            XCTAssertEqual(
                VoiceStatusView.micMode(capability: capability, status: status(state), hiddenByUser: false),
                mode,
                state
            )
        }
    }

    func testMicModeIsHiddenWhenTheUserHidItOrWithoutVoiceOrStatus() {
        for state in [VoiceStatusResponse.stateNotInstalled, VoiceStatusResponse.stateReady] {
            XCTAssertEqual(
                VoiceStatusView.micMode(capability: capability, status: status(state), hiddenByUser: true),
                .hidden,
                state
            )
        }
        XCTAssertEqual(VoiceStatusView.micMode(capability: nil, status: status(), hiddenByUser: false), .hidden)
        XCTAssertEqual(VoiceStatusView.micMode(capability: capability, status: nil, hiddenByUser: false), .hidden)
    }

    func testProgressPercentFloorsCapsAt100AndIsUnknownWithoutATotal() {
        XCTAssertEqual(VoiceStatusView.progressPercent(status(progress: VoiceProgress(done_bytes: 999, total_bytes: 1000))), 99)
        XCTAssertEqual(VoiceStatusView.progressPercent(status(progress: VoiceProgress(done_bytes: 1200, total_bytes: 1000))), 100)
        XCTAssertNil(VoiceStatusView.progressPercent(status(progress: VoiceProgress(done_bytes: 0, total_bytes: 0))))
        XCTAssertNil(VoiceStatusView.progressPercent(status()))
    }
}
