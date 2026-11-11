import XCTest
@testable import Mobaide

/// The terminal drawer's state. The one rule of its own is `synced`: the list
/// reads as agent-authoritative only while the bus is up *and* a snapshot has
/// landed, so a dropped tunnel shows "Reconnecting…" instead of a stale list.
@MainActor
final class SessionsPanelModelTests: XCTestCase {
    private struct Stack {
        let model: SessionsPanelModel
        let sessionRepository: SessionRepository
        let agentRepository: AgentRepository
        let bus: AgentEventBus
        let factory: FakeWebSocketTransportFactory
    }

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeStack(connectedAgentId: String? = "a1") -> Stack {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let factory = FakeWebSocketTransportFactory()
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = AgentAPI(
            baseURL: { "https://agent.test" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        )
        let sessionRepository = SessionRepository(
            agentAPI: agentAPI,
            agentEventBus: bus,
            terminalSessionStore: TerminalTestSupport.store(harness)
        )
        let centralAPI = TerminalTestSupport.stubbedCentralAPI()
        let agentRepository = AgentRepository(centralAPI: centralAPI, userIdentityStore: harness.userIdentityStore)
        let model = SessionsPanelModel(
            sessionRepository: sessionRepository,
            agentEventBus: bus,
            customAgentTypesRepository: CustomAgentTypesRepository(
                centralAPI: centralAPI,
                userIdentityStore: harness.userIdentityStore
            ),
            agentRepository: agentRepository,
            connectedAgentId: connectedAgentId
        )
        return Stack(
            model: model,
            sessionRepository: sessionRepository,
            agentRepository: agentRepository,
            bus: bus,
            factory: factory
        )
    }

    private func snapshot(_ id: String, _ state: String) -> String {
        #"{"type":"sessions.changed","payload":{"sessions":[{"session_id":"\#(id)","state":"\#(state)","created_at":"t"}]}}"#
    }

    private func agentsJSON() -> String {
        #"[{"agent_id":"a1","name":"desktop","online":true,"created_at":"t"},"#
            + #"{"agent_id":"a2","name":"laptop","online":false,"created_at":"t"}]"#
    }

    func testSyncedNeedsBothALiveBusAndASnapshot() async throws {
        let stack = makeStack()
        let observeSynced = Task { await stack.model.observeSessionsSynced() }
        let observeConnection = Task { await stack.model.observeConnection() }
        let observeSessions = Task { await stack.model.observeSessions() }
        defer {
            observeSynced.cancel()
            observeConnection.cancel()
            observeSessions.cancel()
        }
        XCTAssertFalse(stack.model.synced)

        stack.sessionRepository.start()
        await stack.bus.start()
        let connection = try await stack.factory.connection(1)
        connection.emit(.opened)
        // The socket is up but nothing authoritative has arrived yet.
        await waitUntil(timeout: .milliseconds(200)) { false }
        XCTAssertFalse(stack.model.synced, "an open socket alone proves nothing about the list")

        connection.emit(.text(snapshot("s1", "running")))
        await waitUntil { stack.model.synced }
        XCTAssertTrue(stack.model.synced)
        XCTAssertEqual(stack.model.sessions.map(\.session_id), ["s1"])

        connection.emit(.closed(code: 1006, reason: ""))
        await waitUntil { !stack.model.synced }
        XCTAssertFalse(stack.model.synced, "the cached list outlives the tunnel, its authority does not")
        XCTAssertEqual(stack.model.sessions.map(\.session_id), ["s1"], "the rows stay for when the bus returns")
    }

    func testAgentsSplitAroundTheConnectedOne() async {
        let stack = makeStack(connectedAgentId: "a1")
        let observe = Task { await stack.model.observeAgents() }
        defer { observe.cancel() }
        StubURLProtocol.enqueue(status: 200, body: agentsJSON())

        await stack.model.refreshAgents()

        await waitUntil { !stack.model.agents.isEmpty }
        XCTAssertEqual(stack.model.connectedAgent?.name, "desktop")
        XCTAssertEqual(stack.model.otherAgents.map(\.agent_id), ["a2"])
    }

    func testAFailedAgentsRefreshKeepsTheCachedList() async throws {
        let stack = makeStack(connectedAgentId: "a1")
        StubURLProtocol.enqueue(status: 200, body: agentsJSON())
        try await stack.agentRepository.refreshAgents()
        StubURLProtocol.enqueue(status: 500, body: "")

        // Best effort: the drawer must still render whatever the agents screen
        // already fetched.
        await stack.model.refreshAgents()

        XCTAssertEqual(stack.agentRepository.currentAgents.map(\.agent_id), ["a1", "a2"])
    }

    func testInitSeedsSyncedFromALiveBusAndSnapshot() async throws {
        let stack = makeStack()
        let observeSessions = Task { await stack.model.observeSessions() }
        let observeSynced = Task { await stack.model.observeSessionsSynced() }
        let observeConnection = Task { await stack.model.observeConnection() }
        defer {
            observeSessions.cancel()
            observeSynced.cancel()
            observeConnection.cancel()
        }
        stack.sessionRepository.start()
        await stack.bus.start()
        let connection = try await stack.factory.connection(1)
        connection.emit(.opened)
        connection.emit(.text(snapshot("s1", "running")))
        await waitUntil { stack.model.synced }

        let late = SessionsPanelModel(
            sessionRepository: stack.sessionRepository,
            agentEventBus: stack.bus,
            customAgentTypesRepository: CustomAgentTypesRepository(
                centralAPI: TerminalTestSupport.stubbedCentralAPI(),
                userIdentityStore: SessionHarness().userIdentityStore
            ),
            agentRepository: stack.agentRepository,
            connectedAgentId: "a1"
        )

        // A terminal that opens with the drawer up (a stop hand-off) must show
        // the list on its first frame, not "Reconnecting…".
        XCTAssertTrue(late.synced)
        XCTAssertEqual(late.sessions.map(\.session_id), ["s1"])
    }

    func testStopSendsTheDeleteAndReportsSuccess() async throws {
        let stack = makeStack()
        StubURLProtocol.enqueue(status: 204, body: "")

        let stopped = try await stack.model.stopSession("s1", force: true)

        XCTAssertEqual(stopped, SessionsPanelModel.StoppedSession(nextSessionId: nil))
        XCTAssertNil(stack.model.stoppingSessionId)
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "DELETE")
        XCTAssertEqual(request.url?.absoluteString, "https://agent.test/api/sessions/s1?force=true")
    }

    /// Another client, or a push, got there first: the session the user asked
    /// to stop is gone, which is the outcome they wanted.
    func testStopTreatsA404AsAlreadyStopped() async throws {
        let stack = makeStack()
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Session not found"}"#)

        let stopped = try await stack.model.stopSession("s1", force: false)

        XCTAssertEqual(stopped, SessionsPanelModel.StoppedSession(nextSessionId: nil))
        XCTAssertNil(stack.model.stoppingSessionId)
    }

    func testStopRethrowsOtherFailuresAndReenablesTheControls() async {
        let stack = makeStack()
        StubURLProtocol.enqueue(status: 503, body: "agent offline")

        do {
            _ = try await stack.model.stopSession("s1", force: false)
            XCTFail("a 503 must surface as a failure")
        } catch let error as APIError {
            XCTAssertEqual(error.code, 503)
        } catch {
            XCTFail("unexpected error \(error)")
        }
        XCTAssertNil(stack.model.stoppingSessionId)
    }

    func testStopNamesTheNearestOpenSessionFromTheLiveList() async throws {
        let stack = makeStack()
        stack.sessionRepository.start()
        await stack.bus.start()
        let connection = try await stack.factory.connection(1)
        connection.emit(.opened)
        connection.emit(.text(
            #"{"type":"sessions.changed","payload":{"sessions":["#
                + #"{"session_id":"s1","state":"running","created_at":"t"},"#
                + #"{"session_id":"s2","state":"running","created_at":"t"},"#
                + #"{"session_id":"s3","state":"starting","created_at":"t"}]}}"#
        ))
        await waitUntil { stack.sessionRepository.currentSessions.count == 3 }
        StubURLProtocol.enqueue(status: 204, body: "")

        let stopped = try await stack.model.stopSession("s2", force: false)

        XCTAssertEqual(stopped, SessionsPanelModel.StoppedSession(nextSessionId: "s3"))
    }

    private func row(_ id: String, _ state: String = "running") -> SessionResponse {
        makeSessionResponse(id: id, state: state)
    }

    private func nearest(_ snapshot: [SessionResponse], _ current: [SessionResponse], _ stoppedId: String) -> String? {
        SessionsPanelModel.nearestOpenSessionId(snapshot: snapshot, current: current, stoppedId: stoppedId)
    }

    func testNearestPrefersTheRowBelowThenTheRowAbove() {
        let list = [row("a"), row("b"), row("c")]

        XCTAssertEqual(nearest(list, list, "b"), "c")
        XCTAssertEqual(nearest(list, list, "c"), "b")
        XCTAssertEqual(nearest(list, list, "a"), "b")
    }

    func testNearestWidensOutwardPastRowsThatAreNotOpen() {
        let list = [row("a"), row("b", "stopped"), row("c"), row("d", "crashed"), row("e")]

        // One away on both sides is closed; two below beats two above.
        XCTAssertEqual(nearest(list, list, "c"), "e")
        XCTAssertEqual(nearest(list, [row("a"), row("c"), row("d", "crashed")], "c"), "a")
    }

    /// The agent's push can drop the stopped row before the stop resolves.
    func testNearestKeepsTheSnapshotPositionsAfterTheStoppedRowLeft() {
        let snapshot = [row("a"), row("b"), row("c"), row("d")]

        XCTAssertEqual(nearest(snapshot, [row("a"), row("c"), row("d")], "b"), "c")
    }

    func testNearestSkipsANeighbourThatHasSinceGoneOrStopped() {
        let snapshot = [row("a"), row("b"), row("c"), row("d")]

        XCTAssertEqual(nearest(snapshot, [row("a"), row("b"), row("d")], "b"), "a")
        XCTAssertEqual(nearest(snapshot, [row("a", "stopped"), row("c", "stopping"), row("d")], "b"), "d")
    }

    func testNearestIgnoresSessionsThatOnlyAppearedAfterTheSnapshot() {
        let snapshot = [row("a", "stopped"), row("b")]

        XCTAssertNil(nearest(snapshot, [row("a", "stopped"), row("z")], "b"))
    }
}
