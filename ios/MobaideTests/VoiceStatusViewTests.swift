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

    func testPreparingLabelNamesEachPreparingState() {
        XCTAssertEqual(
            VoiceStatusView.preparingLabel(status(VoiceStatusResponse.stateQueued)),
            "Waiting for another voice install on this computer…"
        )
        XCTAssertEqual(
            VoiceStatusView.preparingLabel(status(VoiceStatusResponse.stateVerifying)),
            "Checking voice input…"
        )
        XCTAssertEqual(
            VoiceStatusView.preparingLabel(
                status(VoiceStatusResponse.stateDownloading, progress: VoiceProgress(done_bytes: 43_900, total_bytes: 100_000))
            ),
            "Preparing voice… 43%"
        )
        XCTAssertEqual(VoiceStatusView.preparingLabel(status(VoiceStatusResponse.stateDownloading)), "Preparing voice…")
    }

    func testPreparingLabelShowsWhatIsLeftOfARepair() {
        let repairing = status(
            VoiceStatusResponse.stateDownloading,
            repairing: true,
            progress: VoiceProgress(done_bytes: 670_000_000, total_bytes: 702_000_000)
        )

        XCTAssertEqual(VoiceStatusView.preparingLabel(repairing), "Updating voice (≈32 MB)…")
    }

    func testProgressPercentFloorsCapsAt100AndIsUnknownWithoutATotal() {
        XCTAssertEqual(VoiceStatusView.progressPercent(status(progress: VoiceProgress(done_bytes: 999, total_bytes: 1000))), 99)
        XCTAssertEqual(VoiceStatusView.progressPercent(status(progress: VoiceProgress(done_bytes: 1200, total_bytes: 1000))), 100)
        XCTAssertNil(VoiceStatusView.progressPercent(status(progress: VoiceProgress(done_bytes: 0, total_bytes: 0))))
        XCTAssertNil(VoiceStatusView.progressPercent(status()))
    }

    func testFormatBytesUsesDecimalUnits() {
        XCTAssertEqual(VoiceStatusView.formatBytes(999), "999 B")
        XCTAssertEqual(VoiceStatusView.formatBytes(1_000), "1 KB")
        XCTAssertEqual(VoiceStatusView.formatBytes(670_000_000), "670 MB")
        XCTAssertEqual(VoiceStatusView.formatBytes(1_200_000_000), "1.2 GB")
    }

    func testFormatDurationAndLanguageNames() {
        XCTAssertEqual(VoiceStatusView.formatDuration(ms: 245_999), "4:05")
        XCTAssertEqual(VoiceStatusView.formatDuration(ms: -5), "0:00")
        XCTAssertEqual(VoiceStatusView.languageNames(["en", "de", "fr"]), ["English", "French", "German"])
    }
}
