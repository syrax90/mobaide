import XCTest
@testable import Mobaide

/// Which engine transcribes an agent's dictations and how the session mic
/// shows it.
final class VoiceRouteTests: XCTestCase {
    private let agent = "a1b2c3d4e5f6"

    private func cloud(_ state: String, engines: [String: String] = [:]) -> CloudVoiceStatus {
        CloudVoiceStatus(
            state: state,
            limits: CloudVoiceLimits(sample_rate: 16_000, max_seconds: 120, max_chunk_bytes: 65_536),
            usage: CloudVoiceUsage(used_seconds: 0, limit_seconds: 1800, resets_at: "2026-11-23T00:00:00Z"),
            engines: engines
        )
    }

    private func local(enabled: Bool) -> VoiceStatusResponse {
        VoiceStatusResponse(
            state: enabled ? VoiceStatusResponse.stateReady : VoiceStatusResponse.stateNotInstalled,
            enabled: enabled,
            repairing: false,
            model: VoiceModelInfo(
                id: "parakeet",
                name: "Parakeet",
                languages: ["en"],
                download_bytes: 670_000_000,
                disk_bytes: 700_000_000,
                ram_bytes: 1_200_000_000,
                notice: "CC-BY-4.0"
            ),
            progress: nil,
            host: VoiceHostInfo(
                ram_total_bytes: nil,
                disk_free_bytes: nil,
                cpu_count: nil,
                recommendation: "ok",
                reasons: []
            )
        )
    }

    // MARK: - Route

    func testStaysOnTheComputerWhileCloudIsNotOfferedWhateverWasChosen() {
        let chosen = [agent: "cloud"]
        let notOffered: [CloudVoiceStatus?] = [
            nil,
            cloud(CloudVoiceStatus.stateOff, engines: chosen),
            cloud("some_future_state", engines: chosen),
        ]
        for status in notOffered {
            let name = status?.state ?? "no status"
            XCTAssertEqual(VoiceRoute.resolve(cloud: status, agentId: agent, localStatus: nil), .local, name)
            XCTAssertEqual(
                VoiceRoute.resolve(cloud: status, agentId: agent, localStatus: local(enabled: false)), .local, name
            )
        }
        // Direct-connection mode: no agent id to store a choice under.
        XCTAssertEqual(
            VoiceRoute.resolve(
                cloud: cloud(CloudVoiceStatus.stateReady, engines: chosen), agentId: nil, localStatus: nil
            ),
            .local
        )
    }

    func testFollowsTheStoredChoiceOverTheAgentsOwnVoice() {
        let expected: [(String, VoiceRoute)] = [("cloud", .cloud), ("local", .local)]
        for (engine, route) in expected {
            let status = cloud(CloudVoiceStatus.stateReady, engines: [agent: engine])
            XCTAssertEqual(
                VoiceRoute.resolve(cloud: status, agentId: agent, localStatus: local(enabled: true)), route, engine
            )
            XCTAssertEqual(
                VoiceRoute.resolve(cloud: status, agentId: agent, localStatus: local(enabled: false)), route, engine
            )
            XCTAssertEqual(VoiceRoute.resolve(cloud: status, agentId: agent, localStatus: nil), route, engine)
        }
    }

    func testKeepsACloudChoiceWhileCloudCannotRecord() {
        for state in [CloudVoiceStatus.stateUnavailable, CloudVoiceStatus.stateLimitReached] {
            let status = cloud(state, engines: [agent: "cloud"])
            XCTAssertEqual(
                VoiceRoute.resolve(cloud: status, agentId: agent, localStatus: local(enabled: true)), .cloud, state
            )
        }
    }

    func testWithoutAChoiceKeepsEnabledOnComputerVoiceAndAsksOtherwise() {
        let ready = cloud(CloudVoiceStatus.stateReady)
        XCTAssertEqual(VoiceRoute.resolve(cloud: ready, agentId: agent, localStatus: local(enabled: true)), .local)
        XCTAssertEqual(VoiceRoute.resolve(cloud: ready, agentId: agent, localStatus: local(enabled: false)), .choose)
        XCTAssertEqual(VoiceRoute.resolve(cloud: ready, agentId: agent, localStatus: nil), .choose)
    }

    func testReadsAnotherAgentsChoiceOrAnUnknownEngineAsNoChoice() {
        let others = cloud(CloudVoiceStatus.stateReady, engines: ["other": "cloud"])
        let unknown = cloud(CloudVoiceStatus.stateReady, engines: [agent: "remote"])
        XCTAssertEqual(VoiceRoute.resolve(cloud: others, agentId: agent, localStatus: nil), .choose)
        XCTAssertEqual(VoiceRoute.resolve(cloud: unknown, agentId: agent, localStatus: nil), .choose)
        XCTAssertEqual(VoiceRoute.resolve(cloud: unknown, agentId: agent, localStatus: local(enabled: true)), .local)
    }

    // MARK: - Session mic

    func testOnTheLocalRouteTheMicIsTheOnComputerMic() {
        let ready = cloud(CloudVoiceStatus.stateReady)
        let modes: [VoiceMicMode] = [.hidden, .off, .preparing, .failed, .ready]
        for localMode in modes {
            XCTAssertEqual(
                VoiceStatusView.composerMicMode(route: .local, cloud: ready, localMode: localMode, hiddenByUser: false),
                localMode
            )
        }
    }

    func testOnTheCloudRouteTheMicRecordsOnlyWhenReady() {
        let expected: [(String, VoiceMicMode)] = [
            (CloudVoiceStatus.stateReady, .ready),
            (CloudVoiceStatus.stateUnavailable, .unavailable),
            (CloudVoiceStatus.stateLimitReached, .unavailable),
        ]
        for (state, mode) in expected {
            // Whatever the on-computer mic would be.
            let localModes: [VoiceMicMode] = [.hidden, .failed]
            for localMode in localModes {
                XCTAssertEqual(
                    VoiceStatusView.composerMicMode(
                        route: .cloud, cloud: cloud(state), localMode: localMode, hiddenByUser: false
                    ),
                    mode,
                    state
                )
            }
        }
    }

    func testWithoutAChoiceTheMicOpensTheChooserAndHidingHidesEveryRoute() {
        let ready = cloud(CloudVoiceStatus.stateReady)
        XCTAssertEqual(
            VoiceStatusView.composerMicMode(route: .choose, cloud: ready, localMode: .hidden, hiddenByUser: false),
            .off
        )
        let routes: [VoiceRoute] = [.cloud, .choose]
        for route in routes {
            XCTAssertEqual(
                VoiceStatusView.composerMicMode(route: route, cloud: ready, localMode: .ready, hiddenByUser: true),
                .hidden
            )
        }
    }
}
