import XCTest
@testable import Mobaide

@MainActor
final class SessionRepositoryTests: XCTestCase {
    private struct StubAgentTokens: AgentTokenProviding {
        func validToken() async -> AgentToken? { nil }
        func refreshedToken() async -> String? { nil }
    }

    private func makeStack() -> (SessionRepository, AgentEventBus, FakeWebSocketTransportFactory, TerminalSessionStore) {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let factory = FakeWebSocketTransportFactory()
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
        let store = TerminalTestSupport.store(harness)
        let repo = SessionRepository(agentAPI: agentAPI, agentEventBus: bus, terminalSessionStore: store)
        return (repo, bus, factory, store)
    }

    private func session(_ id: String, state: String = "running", attention: String? = nil) -> String {
        var fields = #""session_id":"\#(id)","state":"\#(state)","created_at":"t""#
        if let attention { fields += #","attention":"\#(attention)""# }
        return "{\(fields)}"
    }

    private func sessionsChanged(_ ids: [String]) -> String {
        let payload = ids.map { session($0) }.joined(separator: ",")
        return #"{"type":"sessions.changed","payload":{"sessions":[\#(payload)]}}"#
    }

    private func sessionUpdated(_ id: String, state: String = "running", attention: String? = nil) -> String {
        #"{"type":"session.updated","payload":{"session":\#(session(id, state: state, attention: attention))}}"#
    }

    private func sessionAttention(_ id: String, attention: String) -> String {
        #"{"type":"session.attention","payload":{"session":\#(session(id, attention: attention))}}"#
    }

    private func waitFor(
        _ repo: SessionRepository,
        _ predicate: @escaping ([SessionResponse]) -> Bool
    ) async -> [SessionResponse] {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if predicate(repo.currentSessions) { return repo.currentSessions }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return repo.currentSessions
    }

    func testSessionsChangedReplacesSnapshot() async throws {
        let (repo, bus, factory, _) = makeStack()
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        connection.emit(.text(sessionsChanged(["a", "b"])))
        var list = await waitFor(repo) { $0.count == 2 }
        XCTAssertEqual(list.map(\.session_id), ["a", "b"])

        connection.emit(.text(sessionsChanged(["c"])))
        list = await waitFor(repo) { $0.map(\.session_id) == ["c"] }
        XCTAssertEqual(list.map(\.session_id), ["c"], "changed snapshot replaces the whole list")
    }

    func testSessionUpdatedUpsertsById() async throws {
        let (repo, bus, factory, _) = makeStack()
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        connection.emit(.text(sessionsChanged(["a", "b"])))
        _ = await waitFor(repo) { $0.count == 2 }

        connection.emit(.text(sessionUpdated("a", state: "stopped")))
        let updated = await waitFor(repo) { $0.first(where: { $0.session_id == "a" })?.state == "stopped" }
        XCTAssertEqual(updated.count, 2, "update replaces in place, not appends")
        XCTAssertEqual(updated.first(where: { $0.session_id == "a" })?.state, "stopped")

        connection.emit(.text(sessionUpdated("z", state: "starting")))
        let appended = await waitFor(repo) { $0.contains(where: { $0.session_id == "z" }) }
        XCTAssertEqual(appended.count, 3, "unknown id is appended")
    }

    func testSessionAttentionUpsertsAttention() async throws {
        let (repo, bus, factory, _) = makeStack()
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        connection.emit(.text(sessionsChanged(["a"])))
        _ = await waitFor(repo) { $0.count == 1 }

        connection.emit(.text(sessionAttention("a", attention: "awaiting_permission")))
        let list = await waitFor(repo) { $0.first?.attention == "awaiting_permission" }
        XCTAssertEqual(list.first?.attention, "awaiting_permission")
    }

    func testStreamMirrorsCurrentSessions() async throws {
        let (repo, bus, factory, _) = makeStack()
        let collector = StreamCollector(repo.sessions())
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        connection.emit(.text(sessionsChanged(["a"])))

        let hasOneSession: @Sendable ([SessionResponse]) -> Bool = { $0.count == 1 }
        let snapshot = try await collector.next(where: hasOneSession)
        XCTAssertEqual(snapshot.map(\.session_id), ["a"])
    }

    func testSnapshotRemovalEvictsTerminalModel() async throws {
        let (repo, bus, factory, store) = makeStack()
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        connection.emit(.text(sessionsChanged(["a", "b"])))
        _ = await waitFor(repo) { $0.count == 2 }

        let modelA = store.model(id: "a", kind: .claude, initialStatus: nil)
        connection.emit(.text(sessionsChanged(["b"])))
        _ = await waitFor(repo) { $0.map(\.session_id) == ["b"] }

        let rebuiltA = store.model(id: "a", kind: .claude, initialStatus: nil)
        XCTAssertFalse(modelA === rebuiltA, "a session dropped from the snapshot must be evicted from the store")
    }

    func testCreateSessionPublishesResponseWithKind() async throws {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"new","state":"running","created_at":"t","kind":"codex"}"#
        )
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(baseURL: { "http://agent.example.com" }, tokens: FakeAgentTokens(token: AgentToken(value: "tok")), client: client)
        let repo = SessionRepository(agentAPI: agentAPI, agentEventBus: bus, terminalSessionStore: TerminalTestSupport.store(harness))

        let response = try await repo.createSession(CreateSessionRequest(project_name: "p", cli: "codex"))

        XCTAssertEqual(response.kind, .codex)
        XCTAssertTrue(
            repo.currentSessions.contains { $0.session_id == "new" && $0.kind == .codex },
            "created session must be published to currentSessions before the caller navigates into its terminal"
        )
    }
}
