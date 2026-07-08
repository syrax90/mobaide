import XCTest
@testable import Mobaide

@MainActor
final class VersionGateRepositoryTests: XCTestCase {
    private var harness: SessionHarness!
    private var gate: VersionGateRepository!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        harness = SessionHarness()
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let centralAPI = CentralAPI(
            centralServerURL: "https://central.example.com",
            sessionInvalidator: SessionInvalidatorAdapter(sessionManager: harness.sessionManager),
            client: client
        )
        gate = VersionGateRepository(
            centralAPI: centralAPI,
            sessionManager: harness.sessionManager,
            appProtocolVersion: 6
        )
    }

    private func versionInfo(protocols: [Int], min: Int, latestAgent: String? = nil) -> VersionInfoResponse {
        VersionInfoResponse(
            name: "central_server",
            server_version: "1.2.3",
            protocol_versions: protocols,
            min_client_version: min,
            latest_agent_version: latestAgent
        )
    }

    // MARK: - classify matrix (app protocol = 6)

    func testCompatibleWhenAtOrAboveMinAndInProtocolSet() {
        let outcome = gate.classify(versionInfo(protocols: [5, 6], min: 5, latestAgent: "0.3.0"))
        XCTAssertEqual(outcome, .compatible(serverVersion: "1.2.3", protocolRange: [5, 6], latestAgentVersion: "0.3.0"))
    }

    func testIncompatibleWhenBelowMinClientVersion() {
        let outcome = gate.classify(versionInfo(protocols: [6, 7], min: 7))
        guard case .incompatible(_, let minClient) = outcome else { return XCTFail("expected incompatible") }
        XCTAssertEqual(minClient, 7)
    }

    func testIncompatibleWhenNotInProtocolSetEvenIfAboveMin() {
        // app=6 >= min=5, but 6 ∉ [5,7] → incompatible: exact set membership,
        // NOT a `<= max(protocol_versions)` range check.
        let outcome = gate.classify(versionInfo(protocols: [5, 7], min: 5))
        guard case .incompatible = outcome else { return XCTFail("expected incompatible") }
    }

    func testCompatibleWithExactSingletonProtocol() {
        let outcome = gate.classify(versionInfo(protocols: [6], min: 6))
        guard case .compatible = outcome else { return XCTFail("expected compatible") }
    }

    func testIncompatibleWhenProtocolListEmpty() {
        let outcome = gate.classify(versionInfo(protocols: [], min: 1))
        guard case .incompatible = outcome else { return XCTFail("expected incompatible") }
    }

    // MARK: - probe side effects

    func testProbeIncompatibleMarksTakeover() async {
        StubURLProtocol.enqueue(status: 200, body: """
        {"name":"central_server","server_version":"9.9.9","protocol_versions":[99],"min_client_version":99,"capabilities":{},"latest_agent_version":null}
        """)

        let outcome = await gate.probe()

        guard case .incompatible = outcome else { return XCTFail("expected incompatible") }
        let state = await harness.sessionManager.currentAuthState
        guard case .incompatibleServer(let info) = state else { return XCTFail("expected takeover state") }
        XCTAssertEqual(info.minClientVersion, 99)
        XCTAssertTrue(info.reason.contains("v99"), "reason surfaces the server minimum")
        XCTAssertEqual(info.storeUrl, AppConfig.appStoreURL)
        XCTAssertEqual(info.storeFallbackUrl, AppConfig.appStoreFallbackURL)
    }

    func testProbeCompatibleLeavesStateUntouched() async {
        StubURLProtocol.enqueue(status: 200, body: """
        {"name":"central_server","server_version":"1.0.0","protocol_versions":[6],"min_client_version":6,"capabilities":{},"latest_agent_version":null}
        """)

        let outcome = await gate.probe()

        guard case .compatible = outcome else { return XCTFail("expected compatible") }
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .unauthenticated)
    }

    func testProbeNetworkErrorIsUnknownAndDoesNotBlock() async {
        StubURLProtocol.enqueue(status: 500, body: "boom")

        let outcome = await gate.probe()

        XCTAssertEqual(outcome, .unknown)
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .unauthenticated, "a probe failure must never gate the app")
    }
}
