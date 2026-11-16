import XCTest
@testable import Mobaide

/// `HerdrError`: the refusals the Herdr screens branch on.
final class HerdrErrorTests: XCTestCase {
    private func refusal(_ status: Int, _ body: String) -> HerdrError {
        HerdrError(APIError(code: status, errorBody: body))
    }

    func testCodedRefusalLiftsTheCodeAndTheAgentsOwnWords() {
        let error = refusal(404, #"{"detail":{"code":"pane_not_found","message":"This pane is closed."}}"#)

        XCTAssertEqual(error, HerdrError(code: "pane_not_found", message: "This pane is closed."))
        XCTAssertTrue(error.isGone)
    }

    func testAStoppedServerIsGoneAndAnyOtherCodeIsNot() {
        XCTAssertTrue(refusal(404, #"{"detail":{"code":"server_not_found","message":"x"}}"#).isGone)

        let refused = refusal(409, #"{"detail":{"code":"herdr_refused","message":"busy"}}"#)
        XCTAssertFalse(refused.isGone)
        XCTAssertEqual(refused.message, "busy")
    }

    func testRefusalWithoutACodedDetailIsUncodedAndStillHasAMessage() {
        XCTAssertEqual(
            refusal(503, #"{"detail":"Agent offline"}"#),
            HerdrError(code: nil, message: "Agent offline")
        )
        for body in [
            "<html>Bad gateway</html>",
            #"{"detail":[{"loc":["pane_id"],"msg":"too long"}]}"#,
            #"{"detail":{"code":"herdr_refused","message":""}}"#,
            #"{"detail":""}"#,
        ] {
            let error = refusal(502, body)
            XCTAssertEqual(error.message, HerdrError.fallbackMessage, body)
            XCTAssertFalse(error.isGone, body)
        }
    }

    func testAnErrorThatIsNotAnAPIErrorIsAHerdrError() {
        let error = HerdrError(URLError(.timedOut))

        XCTAssertEqual(error, HerdrError(code: nil, message: HerdrError.fallbackMessage))
    }
}

/// `HerdrRepository`: whose servers the dashboard shows, and what it asks an
/// agent that has no dashboard.
@MainActor
final class HerdrRepositoryTests: XCTestCase {
    private final class AgentIdBox: @unchecked Sendable {
        private let lock = NSLock()
        private var value: String? = "agent-1"
        var current: String? {
            get { lock.withLock { value } }
            set { lock.withLock { value = newValue } }
        }
    }

    private static let withDashboard = #"""
    {"name":"mobaide_agent","server_version":"1.4.0","protocol_versions":[7],"min_client_version":1,
     "capabilities":{"herdr":{"dashboard":true}}}
    """#
    private static let withoutDashboard = #"""
    {"name":"mobaide_agent","server_version":"1.3.0","protocol_versions":[7],"min_client_version":1,
     "capabilities":{"voice":{"dictation":true}}}
    """#
    private static let oneServer = #"{"servers":[{"server_id":"tui:default"}]}"#
    private static let sessionsChanged = #"{"type":"sessions.changed","payload":{"sessions":[]}}"#
    private static let herdrChanged = #"{"type":"herdr.changed","payload":{"servers":[{"server_id":"tui:default"}]}}"#
    private static let newerHerdrChanged = #"{"type":"herdr.changed","payload":{"servers":[{"server_id":"core:default"}]}}"#
    private static let newerState = HerdrSnapshot(servers: [HerdrServer(server_id: "core:default")])

    private let agentId = AgentIdBox()

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeRepository() -> HerdrRepository { makeStack().0 }

    private func makeStack() -> (HerdrRepository, AgentEventBus, FakeWebSocketTransportFactory) {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: client
        )
        let factory = FakeWebSocketTransportFactory()
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let repository = HerdrRepository(
            agentAPI: agentAPI, agentEventBus: bus, connectedAgentId: { [agentId] in agentId.current }
        )
        return (repository, bus, factory)
    }

    /// The repository on an open events socket, as after the app connects to an agent.
    private func makeConnectedStack() async throws -> (HerdrRepository, FakeWebSocketConnection) {
        let (repository, bus, factory) = makeStack()
        repository.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        return (repository, connection)
    }

    private func waitFor(
        _ repository: HerdrRepository,
        _ predicate: @escaping (HerdrSnapshot?) -> Bool
    ) async -> HerdrSnapshot? {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if predicate(repository.currentSnapshot) { return repository.currentSnapshot }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return repository.currentSnapshot
    }

    private var requestPaths: [String] { StubURLProtocol.requests.compactMap { $0.url?.path } }

    func testRefreshWithoutTheCapabilityAsksForNothing() async {
        // An agent older than the dashboard has no `/api/herdr`.
        StubURLProtocol.enqueue(status: 200, body: Self.withoutDashboard)
        let repository = makeRepository()

        await repository.refresh()

        XCTAssertNil(repository.currentSnapshot)
        XCTAssertEqual(requestPaths, ["/api/version"])
    }

    func testRefreshSeedsTheSnapshot() async {
        StubURLProtocol.enqueue(status: 200, body: Self.withDashboard)
        StubURLProtocol.enqueue(status: 200, body: Self.oneServer)
        let repository = makeRepository()

        await repository.refresh()

        XCTAssertEqual(repository.currentSnapshot, HerdrSnapshot(servers: [HerdrServer(server_id: "tui:default")]))
        XCTAssertEqual(requestPaths, ["/api/version", "/api/herdr"])
    }

    func testRefreshForAnotherAgentDropsThePreviousAgentsServers() async {
        StubURLProtocol.enqueue(status: 200, body: Self.withDashboard)
        StubURLProtocol.enqueue(status: 200, body: Self.oneServer)
        let repository = makeRepository()
        await repository.refresh()
        XCTAssertNotNil(repository.currentSnapshot)

        // The next agent has no dashboard: the servers of the first one must
        // not stay on screen under its name.
        agentId.current = "agent-2"
        StubURLProtocol.enqueue(status: 200, body: Self.withoutDashboard)
        await repository.refresh()

        XCTAssertNil(repository.currentSnapshot)
    }

    func testRefreshKeepsTheServersWhenTheReadFailsAndDropsThemWhenTheAgentHasNoDashboardAnyMore() async {
        StubURLProtocol.enqueue(status: 200, body: Self.withDashboard)
        StubURLProtocol.enqueue(status: 200, body: Self.oneServer)
        let repository = makeRepository()
        await repository.refresh()
        let seeded = HerdrSnapshot(servers: [HerdrServer(server_id: "tui:default")])
        XCTAssertEqual(repository.currentSnapshot, seeded)

        // A read that failed says nothing about the agent: a bad connection
        // must not empty the dashboard.
        StubURLProtocol.enqueue(status: 503, body: #"{"detail":"Agent offline"}"#)
        await repository.refresh()
        XCTAssertEqual(repository.currentSnapshot, seeded)

        // The same agent answers, and has no dashboard any more.
        StubURLProtocol.enqueue(status: 200, body: Self.withoutDashboard)
        await repository.refresh()
        XCTAssertNil(repository.currentSnapshot)
        XCTAssertEqual(requestPaths, ["/api/version", "/api/herdr", "/api/version", "/api/version"])
    }

    func testSessionsChangedWhileServersAreHeldAsksTheAgentAgain() async throws {
        StubURLProtocol.enqueue(status: 200, body: Self.withDashboard)
        StubURLProtocol.enqueue(status: 200, body: Self.oneServer)
        let (repository, connection) = try await makeConnectedStack()
        await repository.refresh()
        XCTAssertNotNil(repository.currentSnapshot)

        // The agent came back without the dashboard: it sends its session list
        // on connect, and no `herdr.changed` that would correct the screen.
        StubURLProtocol.enqueue(status: 200, body: Self.withoutDashboard)
        connection.emit(.text(Self.sessionsChanged))

        let snapshot = await waitFor(repository) { $0 == nil }
        XCTAssertNil(snapshot)
        XCTAssertEqual(requestPaths, ["/api/version", "/api/herdr", "/api/version"])
    }

    func testSessionsChangedWhileNoServersAreHeldAsksTheAgentNothing() async throws {
        let (repository, connection) = try await makeConnectedStack()

        connection.emit(.text(Self.sessionsChanged))
        // One loop handles the events in order: when this state is in, the
        // session list before it was handled to its end.
        connection.emit(.text(Self.herdrChanged))
        let snapshot = await waitFor(repository) { $0 != nil }

        XCTAssertEqual(snapshot, HerdrSnapshot(servers: [HerdrServer(server_id: "tui:default")]))
        // An agent without the dashboard gets no version read per session event.
        XCTAssertEqual(requestPaths, [])
    }

    func testTheSessionsChangedCheckReadsOnlyTheVersionAndKeepsThePushedState() async throws {
        // The state arrives as `herdr.changed`. A REST copy read by the check
        // could be older than the pushed one and replace it.
        StubURLProtocol.enqueue(status: 200, body: Self.withDashboard)
        StubURLProtocol.enqueue(status: 200, body: Self.oneServer)
        let (repository, connection) = try await makeConnectedStack()
        await repository.refresh()
        connection.emit(.text(Self.newerHerdrChanged))
        let pushed = await waitFor(repository) { $0 == Self.newerState }
        XCTAssertEqual(pushed, Self.newerState)

        StubURLProtocol.enqueue(status: 200, body: Self.withDashboard)
        StubURLProtocol.enqueue(status: 200, body: Self.withDashboard)
        connection.emit(.text(Self.sessionsChanged))
        await waitUntil { StubURLProtocol.requests.count == 3 }
        // The checks run one at a time: the read of a second check starts
        // when the first check ran to its end.
        connection.emit(.text(Self.sessionsChanged))
        await waitUntil { StubURLProtocol.requests.count == 4 }

        XCTAssertEqual(requestPaths, ["/api/version", "/api/herdr", "/api/version", "/api/version"])
        XCTAssertEqual(repository.currentSnapshot, Self.newerState)
    }

    func testARefreshWhoseReadIsOvertakenByAPushKeepsThePushedState() async throws {
        // A pane closed during the read leaves on the push. The older REST
        // answer must not bring it back.
        let (repository, connection) = try await makeConnectedStack()
        StubURLProtocol.enqueue(status: 200, body: Self.withDashboard)
        StubURLProtocol.enqueue(status: 200, body: Self.oneServer)
        // Holds the `/api/herdr` read on the loading thread.
        let gate = DispatchSemaphore(value: 0)
        StubURLProtocol.onRequest = { request in
            guard request.url?.path == "/api/herdr" else { return }
            StubURLProtocol.onRequest = nil
            gate.wait()
        }

        let refreshing = Task { await repository.refresh() }
        await waitUntil { StubURLProtocol.requests.count == 2 }
        connection.emit(.text(Self.newerHerdrChanged))
        let pushed = await waitFor(repository) { $0 == Self.newerState }
        XCTAssertEqual(pushed, Self.newerState)

        gate.signal()
        await refreshing.value

        XCTAssertEqual(repository.currentSnapshot, Self.newerState)
    }

    func testABurstOfSessionsChangedRunsOneCheckAtATimeAndDoesNotHoldBackTheState() async throws {
        StubURLProtocol.enqueue(status: 200, body: Self.withDashboard)
        StubURLProtocol.enqueue(status: 200, body: Self.oneServer)
        let (repository, connection) = try await makeConnectedStack()
        await repository.refresh()
        // The first check answers that the dashboard is there; the one that
        // stands for the rest of the burst answers that it is gone.
        StubURLProtocol.enqueue(status: 200, body: Self.withDashboard)
        StubURLProtocol.enqueue(status: 200, body: Self.withoutDashboard)
        // Holds the first version read on the loading thread.
        let gate = DispatchSemaphore(value: 0)
        StubURLProtocol.onRequest = { _ in
            StubURLProtocol.onRequest = nil
            gate.wait()
        }

        connection.emit(.text(Self.sessionsChanged))
        await waitUntil { StubURLProtocol.requests.count == 3 }
        for _ in 0..<5 { connection.emit(.text(Self.sessionsChanged)) }
        // One loop handles the events in order: when this state is in, the
        // five session lists before it were handled.
        connection.emit(.text(Self.newerHerdrChanged))
        let pushed = await waitFor(repository) { $0 == Self.newerState }

        XCTAssertEqual(pushed, Self.newerState, "a check in flight must not hold back herdr.changed")
        XCTAssertEqual(StubURLProtocol.requests.count, 3, "no read per event while one is in flight")

        gate.signal()
        let cleared = await waitFor(repository) { $0 == nil }

        XCTAssertNil(cleared)
        XCTAssertEqual(
            requestPaths, ["/api/version", "/api/herdr", "/api/version", "/api/version"],
            "one check stands for the five events of the burst"
        )
    }

    func testARefusalReachesTheScreenAsAHerdrError() async {
        StubURLProtocol.enqueue(
            status: 404,
            body: #"{"detail":{"code":"pane_not_found","message":"This pane is closed."}}"#
        )
        let repository = makeRepository()

        do {
            _ = try await repository.openPane(serverId: "core:default", paneId: "box/t3")
            XCTFail("expected a HerdrError")
        } catch let error as HerdrError {
            XCTAssertEqual(error, HerdrError(code: "pane_not_found", message: "This pane is closed."))
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testAPaneIsNamedInTheBodyAndInTheQueryNeverInThePath() async throws {
        // A pane id can contain `/` and `:`.
        StubURLProtocol.enqueue(status: 200, body: #"{"session_id":"herdr-0123"}"#)
        StubURLProtocol.enqueue(status: 200, body: #"{"text":"last line","truncated":true}"#)
        let repository = makeRepository()

        let sessionId = try await repository.openPane(serverId: "core:default", paneId: "box/t3&x=1")
        let text = try await repository.paneText(serverId: "core:default", paneId: "box/t3&x=1", lines: 1000)

        XCTAssertEqual(sessionId, "herdr-0123")
        XCTAssertEqual(text.text, "last line")
        XCTAssertTrue(text.truncated)
        XCTAssertEqual(requestPaths, ["/api/herdr/pane/open", "/api/herdr/pane/text"])
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["server_id": "core:default", "pane_id": "box/t3&x=1"])
        let query = try XCTUnwrap(
            URLComponents(url: try XCTUnwrap(StubURLProtocol.requests[1].url), resolvingAgainstBaseURL: false)?
                .queryItems
        )
        XCTAssertEqual(
            Dictionary(uniqueKeysWithValues: query.map { ($0.name, $0.value ?? "") }),
            ["server_id": "core:default", "pane_id": "box/t3&x=1", "lines": "1000"]
        )
    }
}

/// `capabilities.herdr.dashboard` hides or shows the whole Herdr UI.
final class VersionDtoHerdrCapabilityTests: XCTestCase {
    private func capability(_ herdr: JSONValue?) -> Bool {
        var capabilities: [String: JSONValue] = ["voice": ["dictation": true]]
        if let herdr { capabilities["herdr"] = herdr }
        return VersionInfoResponse(
            name: "mobaide-agent",
            server_version: "1.4.0",
            protocol_versions: [7],
            min_client_version: 1,
            capabilities: .object(capabilities)
        ).herdrCapability
    }

    func testReadsAnExplicitTrueAndNothingElse() {
        XCTAssertTrue(capability(["dashboard": true]))
        XCTAssertFalse(capability(["dashboard": "true"]))
        XCTAssertFalse(capability(["dashboard": 1]))
        XCTAssertFalse(capability(["dashboard": false]))
        XCTAssertFalse(capability(true))
        XCTAssertFalse(capability([:]))
        // An agent older than the dashboard sends no key at all.
        XCTAssertFalse(capability(nil))
    }
}
