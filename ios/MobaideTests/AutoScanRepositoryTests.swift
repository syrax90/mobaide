import XCTest
@testable import Mobaide

@MainActor
final class AutoScanRepositoryTests: XCTestCase {
    private struct StubAgentTokens: AgentTokenProviding {
        func validToken() async -> AgentToken? { nil }
        func refreshedToken() async -> String? { nil }
    }

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeWsClient(factory: FakeWebSocketTransportFactory = FakeWebSocketTransportFactory()) -> AutoScanWsClient {
        AutoScanWsClient(
            webSocketFactory: .live(transportFactory: factory, sleeper: RecordingSleeper()),
            urlProvider: { "ws://host/ws/autoscan?token=t" },
            isAuthenticated: { true }
        )
    }

    private func makeBusStack() -> (AutoScanRepository, AgentEventBus, FakeWebSocketTransportFactory) {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let factory = FakeWebSocketTransportFactory()
        let bus = AgentEventBus(
            webSocketFactory: .live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
        let repo = AutoScanRepository(agentAPI: agentAPI, agentEventBus: bus, wsClient: makeWsClient())
        return (repo, bus, factory)
    }

    private func makeRESTRepo() -> AutoScanRepository {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: .live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: client
        )
        return AutoScanRepository(agentAPI: agentAPI, agentEventBus: bus, wsClient: makeWsClient())
    }

    private func statusFrame(name: String, status: String) -> String {
        #"{"type":"autoscan.status","payload":{"project_name":"\#(name)","status":"\#(status)","started_at":1.5,"last_activity_at":1.5}}"#
    }

    private func listChangedFrame(_ scans: [(name: String, status: String)]) -> String {
        let items = scans
            .map { #"{"project_name":"\#($0.name)","status":"\#($0.status)","started_at":1.5,"last_activity_at":1.5}"# }
            .joined(separator: ",")
        return #"{"type":"autoscan.list.changed","payload":{"scans":[\#(items)],"max_scans":5}}"#
    }

    private func waitFor(
        _ repo: AutoScanRepository,
        _ predicate: @escaping (AutoScanListResponse) -> Bool
    ) async -> AutoScanListResponse {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if predicate(repo.currentList) { return repo.currentList }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return repo.currentList
    }

    // MARK: - Bus reconciliation

    func testAutoScanListChangedReplacesWholeList() async throws {
        let (repo, bus, factory) = makeBusStack()
        repo.start()
        await bus.start()
        let conn = try await factory.connection(1)
        conn.emit(.opened)

        conn.emit(.text(listChangedFrame([(name: "a", status: "scanning"), (name: "b", status: "idle")])))
        let list = await waitFor(repo) { $0.scans.count == 2 }
        XCTAssertEqual(list.scans.map(\.project_name), ["a", "b"])
    }

    func testAutoScanStatusReplacesMatchingInstanceInPlace() async throws {
        let (repo, bus, factory) = makeBusStack()
        repo.start()
        await bus.start()
        let conn = try await factory.connection(1)
        conn.emit(.opened)
        conn.emit(.text(listChangedFrame([(name: "a", status: "scanning"), (name: "b", status: "scanning")])))
        _ = await waitFor(repo) { $0.scans.count == 2 }

        conn.emit(.text(statusFrame(name: "a", status: "idle")))
        let list = await waitFor(repo) { $0.scans.first { $0.project_name == "a" }?.status == "idle" }
        XCTAssertEqual(list.scans.first { $0.project_name == "a" }?.status, "idle")
        XCTAssertEqual(list.scans.first { $0.project_name == "b" }?.status, "scanning")
    }

    func testAutoScanStatusForUnknownProjectLeavesListUnchanged() async throws {
        let (repo, bus, factory) = makeBusStack()
        repo.start()
        await bus.start()
        let conn = try await factory.connection(1)
        conn.emit(.opened)
        conn.emit(.text(listChangedFrame([(name: "a", status: "scanning")])))
        _ = await waitFor(repo) { $0.scans.count == 1 }

        conn.emit(.text(statusFrame(name: "zzz", status: "idle")))
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(repo.currentScans.map(\.project_name), ["a"])
        XCTAssertEqual(repo.currentScans.first?.status, "scanning")
    }

    // MARK: - REST seed + mutations

    func testRefreshSeedsFromRESTAndExposesPrompt() async {
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"scans":[{"project_name":"a","status":"scanning","started_at":1.5,"last_activity_at":1.5}],"max_scans":5,"auto_scan_prompt":"do it"}"#
        )
        let repo = makeRESTRepo()
        let list = await repo.refresh()
        XCTAssertEqual(list.scans.map(\.project_name), ["a"])
        XCTAssertEqual(repo.autoScanPrompt, "do it")
    }

    func testStartScanAddsOptimisticPlaceholder() async throws {
        StubURLProtocol.enqueue(status: 200, body: "{}")
        let repo = makeRESTRepo()
        try await repo.startScan(projectName: "p")
        XCTAssertEqual(repo.currentScans.map(\.project_name), ["p"])
        XCTAssertEqual(repo.currentScans.first?.status, "starting")
    }

    func testStartScan409MapsToAlreadyRunningAndRollsBack() async {
        StubURLProtocol.enqueue(status: 409, body: "{}")
        let repo = makeRESTRepo()
        do {
            try await repo.startScan(projectName: "p")
            XCTFail("expected alreadyRunning")
        } catch AutoScanStartError.alreadyRunning(let name) {
            XCTAssertEqual(name, "p")
        } catch {
            XCTFail("unexpected error \(error)")
        }
        XCTAssertTrue(repo.currentScans.isEmpty, "optimistic placeholder must roll back")
    }

    func testStartScan429MapsToCapacity() async {
        StubURLProtocol.enqueue(status: 429, body: "{}")
        let repo = makeRESTRepo()
        do {
            try await repo.startScan(projectName: "p")
            XCTFail("expected capacity")
        } catch AutoScanStartError.capacity(let maxScans) {
            XCTAssertEqual(maxScans, 5)
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testStartScanFailureEmitsErrorContainingProjectName() async throws {
        StubURLProtocol.enqueue(status: 500, body: "boom")
        let repo = makeRESTRepo()
        let collector = StreamCollector(repo.errors())
        try? await repo.startScan(projectName: "myproj")
        let error = try await collector.next()
        XCTAssertTrue(error.message.contains("myproj"), "error message should name the project")
    }

    func testStopScanRemovesThenRollsBackOnError() async throws {
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"scans":[{"project_name":"p1","status":"scanning","started_at":1.5,"last_activity_at":1.5},{"project_name":"p2","status":"scanning","started_at":1.5,"last_activity_at":1.5}],"max_scans":5}"#
        )
        let repo = makeRESTRepo()
        _ = await repo.refresh()
        XCTAssertEqual(repo.currentScans.count, 2)

        StubURLProtocol.enqueue(status: 500, body: "down")
        do {
            try await repo.stopScan(projectName: "p1")
            XCTFail("expected stop to throw")
        } catch {}
        XCTAssertEqual(Set(repo.currentScans.map(\.project_name)), ["p1", "p2"], "stop must roll back on error")
    }

    func testRunNowReconcilesNextScanAtFromResponse() async throws {
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"scans":[{"project_name":"p","status":"scanning","started_at":1.5,"last_activity_at":1.5,"next_scan_at":"T1"}],"max_scans":5}"#
        )
        let repo = makeRESTRepo()
        _ = await repo.refresh()

        StubURLProtocol.enqueue(status: 200, body: #"{"ok":true,"project_name":"p","next_scan_at":"T2"}"#)
        let response = try await repo.runNow(projectName: "p")
        XCTAssertEqual(response.next_scan_at, "T2")
        XCTAssertEqual(repo.currentScans.first?.next_scan_at, "T2")
    }

    // MARK: - Detail-frame delegation

    func testObserveDetailFramesConnectsWithProjectAndResume() async throws {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: .live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
        let wsFactory = FakeWebSocketTransportFactory()
        let repo = AutoScanRepository(agentAPI: agentAPI, agentEventBus: bus, wsClient: makeWsClient(factory: wsFactory))

        let collector = StreamCollector(repo.observeDetailFrames(project: "p", resume: ["p": 9]))
        let conn = try await wsFactory.connection(1)
        conn.emit(.opened)
        _ = try await collector.next()

        let sent = try XCTUnwrap(conn.sentTexts.first)
        let object = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(sent.utf8)) as? [String: Any])
        XCTAssertEqual(object["subscribe"] as? [String], ["p"])
        let resume = try XCTUnwrap(object["resume_from_seq"] as? [String: Any])
        XCTAssertEqual((resume["p"] as? NSNumber)?.int64Value, 9)
    }
}
