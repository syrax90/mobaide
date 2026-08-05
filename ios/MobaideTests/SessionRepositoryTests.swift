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

    private func session(_ id: String, state: String = "running", attention: String? = nil, kind: String? = nil) -> String {
        var fields = #""session_id":"\#(id)","state":"\#(state)","created_at":"t""#
        if let attention { fields += #","attention":"\#(attention)""# }
        if let kind { fields += #","kind":"\#(kind)""# }
        return "{\(fields)}"
    }

    private func sessionsChanged(_ ids: [String]) -> String {
        let payload = ids.map { session($0) }.joined(separator: ",")
        return #"{"type":"sessions.changed","payload":{"sessions":[\#(payload)]}}"#
    }

    private func sessionsChanged(entries: [(id: String, kind: String)]) -> String {
        let payload = entries.map { session($0.id, kind: $0.kind) }.joined(separator: ",")
        return #"{"type":"sessions.changed","payload":{"sessions":[\#(payload)]}}"#
    }

    private func sessionUpdated(_ id: String, state: String = "running", attention: String? = nil, kind: String? = nil) -> String {
        #"{"type":"session.updated","payload":{"session":\#(session(id, state: state, attention: attention, kind: kind))}}"#
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

        let modelA = store.model(id: "a", kind: .claude, resolvedKind: nil, initialStatus: nil)
        connection.emit(.text(sessionsChanged(["b"])))
        _ = await waitFor(repo) { $0.map(\.session_id) == ["b"] }

        let rebuiltA = store.model(id: "a", kind: .claude, resolvedKind: nil, initialStatus: nil)
        XCTAssertFalse(modelA === rebuiltA, "a session dropped from the snapshot must be evicted from the store")
    }

    func testSnapshotAdoptsKindIntoUnresolvedTerminalModel() async throws {
        let (repo, bus, factory, store) = makeStack()
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        // Cold-start deep link: the model exists before any session row loaded
        // and its one-shot resolver found nothing.
        let model = store.model(id: "a", kind: .claude, resolvedKind: nil, initialStatus: nil)
        XCTAssertNil(model.resolvedKind)

        connection.emit(.text(sessionsChanged(entries: [
            (id: "a", kind: "custom_ab12cd34ef56"),
            (id: "ghost", kind: "codex"),
        ])))
        _ = await waitFor(repo) { $0.count == 2 }

        XCTAssertEqual(
            model.resolvedKind, .custom("custom_ab12cd34ef56"),
            "the next sessions.changed snapshot must heal a failed one-shot kind resolution"
        )
        let ghost = store.model(id: "ghost", kind: .claude, resolvedKind: nil, initialStatus: nil)
        XCTAssertNil(ghost.resolvedKind, "adoption for an id with no live model must not create or seed one")
    }

    func testSnapshotKindAdoptionIsFirstWins() async throws {
        let (repo, bus, factory, store) = makeStack()
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        let model = store.model(id: "a", kind: .claude, resolvedKind: nil, initialStatus: nil)

        connection.emit(.text(sessionsChanged(entries: [(id: "a", kind: "codex")])))
        _ = await waitFor(repo) { $0.first?.kind == .codex }
        XCTAssertEqual(model.resolvedKind, .codex)

        connection.emit(.text(sessionsChanged(entries: [(id: "a", kind: "claude")])))
        _ = await waitFor(repo) { $0.first?.kind == .claude }
        XCTAssertEqual(
            model.resolvedKind, .codex,
            "only the first resolution wins — a later snapshot must not flip an adopted kind"
        )
    }

    func testSessionUpdatedUpsertAlsoAdoptsKind() async throws {
        let (repo, bus, factory, store) = makeStack()
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        let model = store.model(id: "a", kind: .claude, resolvedKind: nil, initialStatus: nil)

        connection.emit(.text(sessionUpdated("a", kind: "codex")))
        _ = await waitFor(repo) { $0.contains { $0.session_id == "a" } }

        XCTAssertEqual(
            model.resolvedKind, .codex,
            "the session.updated upsert path funnels through the same kind adoption as full snapshots"
        )
    }

    private func waitForSynced(_ repo: SessionRepository, _ expected: Bool) async -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if repo.sessionsSynced == expected { return true }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return repo.sessionsSynced == expected
    }

    func testSessionsSyncedRequiresASnapshotNotJustAnOpenSocket() async throws {
        let (repo, bus, factory, _) = makeStack()
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        XCTAssertFalse(
            repo.sessionsSynced,
            "the agent replays its snapshot after the socket opens; until it lands the cache is whatever survived the drop"
        )

        connection.emit(.text(sessionsChanged(["a"])))
        let synced = await waitForSynced(repo, true)
        XCTAssertTrue(synced)
    }

    func testSessionUpdatedAloneDoesNotMarkTheListSynced() async throws {
        let (repo, bus, factory, _) = makeStack()
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        connection.emit(.text(sessionUpdated("a")))
        _ = await waitFor(repo) { $0.contains { $0.session_id == "a" } }

        XCTAssertFalse(
            repo.sessionsSynced,
            "a single-session upsert says nothing about the sessions it does not mention"
        )
    }

    func testDisconnectClearsSyncedButKeepsTheList() async throws {
        let (repo, bus, factory, _) = makeStack()
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        connection.emit(.text(sessionsChanged(["a"])))
        let synced = await waitForSynced(repo, true)
        XCTAssertTrue(synced)

        connection.emit(.closed(code: 1006, reason: "drop"))

        let cleared = await waitForSynced(repo, false)
        XCTAssertTrue(
            cleared,
            "after an agent restart the cached list still names sessions it lost"
        )
        XCTAssertEqual(
            repo.currentSessions.map(\.session_id), ["a"],
            "the rows stay on screen; only their authority is revoked"
        )
    }

    func testEndedSessionNoticeIsDeliveredThenCleared() async throws {
        let (repo, _, _, _) = makeStack()
        let collector = StreamCollector(repo.endedSessionNotices())

        repo.notifySessionEnded(id: "gone")

        let delivered = try await collector.next()
        XCTAssertEqual(delivered, "gone")

        repo.clearEndedSessionNotice()
        let replayed = StreamCollector(repo.endedSessionNotices())
        let noReplay = await replayed.expectNoElement(within: .milliseconds(200))
        XCTAssertTrue(noReplay, "a cleared notice must not re-toast on the next subscriber")
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
