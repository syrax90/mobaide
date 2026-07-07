import XCTest
@testable import Mobaide

@MainActor
final class SessionListModelTests: XCTestCase {
    private struct StubAgentTokens: AgentTokenProviding {
        func validToken() async -> AgentToken? { nil }
        func refreshedToken() async -> String? { nil }
    }

    private func makeStack(
        prefs: PreferencesStore
    ) -> (SessionListModel, SessionRepository, AgentEventBus, FakeWebSocketTransportFactory) {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let factory = FakeWebSocketTransportFactory()
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
        let repo = SessionRepository(agentAPI: agentAPI, agentEventBus: bus, terminalSessionStore: TerminalTestSupport.store(harness))
        let model = SessionListModel(repository: repo, agentEventBus: bus, preferencesStore: prefs)
        return (model, repo, bus, factory)
    }

    private func isolatedPrefs() -> PreferencesStore {
        PreferencesStore(defaults: UserDefaults(suiteName: "sl-\(UUID().uuidString)")!)
    }

    private func snapshot(_ id: String, _ state: String) -> String {
        #"{"type":"sessions.changed","payload":{"sessions":[{"session_id":"\#(id)","state":"\#(state)","created_at":"t"}]}}"#
    }

    // MARK: - Display helpers (pure)

    func testKindLabels() {
        XCTAssertEqual(SessionDisplay.kindLabel(.claude), "Claude Code")
        XCTAssertEqual(SessionDisplay.kindLabel(.codex), "Codex")
        XCTAssertEqual(SessionDisplay.kindLabel(.antigravity), "Antigravity")
        XCTAssertEqual(SessionDisplay.kindLabel(.shell), "Terminal")
    }

    func testAttentionMapping() {
        XCTAssertEqual(SessionDisplay.attention("awaiting_permission")?.text, "Needs permission")
        XCTAssertEqual(SessionDisplay.attention("turn_finished")?.text, "Ready")
        XCTAssertEqual(SessionDisplay.attention("ended")?.text, "Ended")
        XCTAssertNil(SessionDisplay.attention(nil))
        XCTAssertNil(SessionDisplay.attention("something_else"))
    }

    func testCanStop() {
        XCTAssertTrue(SessionDisplay.canStop("running"))
        XCTAssertTrue(SessionDisplay.canStop("starting"))
        XCTAssertFalse(SessionDisplay.canStop("stopped"))
        XCTAssertFalse(SessionDisplay.canStop("crashed"))
    }

    // MARK: - Bus-driven list + auto-resume

    func testListReflectsBusSnapshot() async throws {
        let (model, repo, bus, factory) = makeStack(prefs: isolatedPrefs())
        let observe = Task { await model.observeSessions() }
        defer { observe.cancel() }
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        connection.emit(.text(snapshot("a", "running")))

        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline, model.sessions.isEmpty {
            try await Task.sleep(for: .milliseconds(5))
        }
        XCTAssertEqual(model.sessions.map(\.session_id), ["a"])
    }

    func testAutoResumeOpensRunningLastSession() async throws {
        let prefs = isolatedPrefs()
        prefs.setLastUsedSessionId("a")
        let (model, repo, bus, factory) = makeStack(prefs: prefs)
        let collector = StreamCollector(model.autoResumeEvents())
        let observe = Task { await model.observeSessions() }
        defer { observe.cancel() }
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        connection.emit(.text(snapshot("a", "running")))

        let resumed = try await collector.next()
        XCTAssertEqual(resumed, "a")
    }

    func testAutoResumeClearsStoredIdWhenNotRunning() async throws {
        let prefs = isolatedPrefs()
        prefs.setLastUsedSessionId("a")
        let (model, repo, bus, factory) = makeStack(prefs: prefs)
        let collector = StreamCollector(model.autoResumeEvents())
        let observe = Task { await model.observeSessions() }
        defer { observe.cancel() }
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        connection.emit(.text(snapshot("a", "stopped")))

        let noResume = await collector.expectNoElement(within: .milliseconds(300))
        XCTAssertTrue(noResume, "a stopped session must not auto-resume")
        XCTAssertNil(prefs.getLastUsedSessionId(), "stale last-used id is cleared")
    }
}
