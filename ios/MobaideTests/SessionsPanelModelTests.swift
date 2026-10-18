import XCTest
@testable import Mobaide

/// The terminal drawer's state. The one rule of its own is `synced`: the list
/// reads as agent-authoritative only while the bus is up *and* a snapshot has
/// landed, so a dropped tunnel shows "Reconnecting…" instead of a stale list.
@MainActor
final class SessionsPanelModelTests: XCTestCase {
    private struct StubAgentTokens: AgentTokenProviding {
        func validToken() async -> AgentToken? { nil }
        func refreshedToken() async -> String? { nil }
    }

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
        let agentAPI = AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
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

    func testInitSeedsFromTheRepositoryCaches() async throws {
        let stack = makeStack(connectedAgentId: "a2")
        StubURLProtocol.enqueue(status: 200, body: agentsJSON())
        try await stack.agentRepository.refreshAgents()

        let late = SessionsPanelModel(
            sessionRepository: stack.sessionRepository,
            agentEventBus: stack.bus,
            customAgentTypesRepository: CustomAgentTypesRepository(
                centralAPI: TerminalTestSupport.stubbedCentralAPI(),
                userIdentityStore: SessionHarness().userIdentityStore
            ),
            agentRepository: stack.agentRepository,
            connectedAgentId: "a2"
        )

        // A drawer opened after the agents screen fetched must not start blank
        // and wait for the next emission.
        XCTAssertEqual(late.connectedAgent?.name, "laptop")
        XCTAssertEqual(late.otherAgents.map(\.name), ["desktop"])
        XCTAssertFalse(late.synced)
    }

    func testNoConnectedAgentIdListsEveryAgentAsOther() async throws {
        let stack = makeStack(connectedAgentId: nil)
        let observe = Task { await stack.model.observeAgents() }
        defer { observe.cancel() }
        StubURLProtocol.enqueue(status: 200, body: agentsJSON())

        await stack.model.refreshAgents()

        await waitUntil { !stack.model.agents.isEmpty }
        XCTAssertNil(stack.model.connectedAgent)
        XCTAssertEqual(stack.model.otherAgents.map(\.agent_id), ["a1", "a2"])
    }
}
