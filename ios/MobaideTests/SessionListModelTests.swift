import XCTest
@testable import Mobaide

@MainActor
final class SessionListModelTests: XCTestCase {
    private struct StubAgentTokens: AgentTokenProviding {
        func validToken() async -> AgentToken? { nil }
        func refreshedToken() async -> String? { nil }
    }

    private func idleWsClient() -> AutoScanWsClient {
        AutoScanWsClient(
            webSocketFactory: .live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            urlProvider: { nil },
            isAuthenticated: { false }
        )
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
        let autoScanRepo = AutoScanRepository(agentAPI: agentAPI, agentEventBus: bus, wsClient: idleWsClient())
        let model = SessionListModel(
            repository: repo,
            agentEventBus: bus,
            preferencesStore: prefs,
            autoScanRepository: autoScanRepo,
            customAgentTypesRepository: makeTypesRepo(harness: harness)
        )
        return (model, repo, bus, factory)
    }

    private func makeRESTModel() -> SessionListModel {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: client
        )
        let repo = SessionRepository(agentAPI: agentAPI, agentEventBus: bus, terminalSessionStore: TerminalTestSupport.store(harness))
        let autoScanRepo = AutoScanRepository(agentAPI: agentAPI, agentEventBus: bus, wsClient: idleWsClient())
        return SessionListModel(
            repository: repo,
            agentEventBus: bus,
            preferencesStore: isolatedPrefs(),
            autoScanRepository: autoScanRepo,
            customAgentTypesRepository: makeTypesRepo(harness: harness)
        )
    }

    private func makeTypesRepo(harness: SessionHarness) -> CustomAgentTypesRepository {
        CustomAgentTypesRepository(
            centralAPI: TerminalTestSupport.stubbedCentralAPI(),
            userIdentityStore: harness.userIdentityStore
        )
    }

    private func isolatedPrefs() -> PreferencesStore {
        PreferencesStore(defaults: UserDefaults(suiteName: "sl-\(UUID().uuidString)")!)
    }

    private func snapshot(_ id: String, _ state: String) -> String {
        #"{"type":"sessions.changed","payload":{"sessions":[{"session_id":"\#(id)","state":"\#(state)","created_at":"t"}]}}"#
    }

    // MARK: - Display helpers (pure)

    func testCustomKindLabelChain() {
        let names = ["custom_ab12cd34ef56": "Aider Renamed"]

        let withSnapshot = makeSessionResponse(kind: .custom("custom_ab12cd34ef56"), kindLabel: "Aider")
        XCTAssertEqual(
            SessionDisplay.kindLabel(withSnapshot, customTypeNames: names), "Aider",
            "the creation-time kind_label snapshot wins over the live registry name"
        )

        let withoutSnapshot = makeSessionResponse(kind: .custom("custom_ab12cd34ef56"))
        XCTAssertEqual(
            SessionDisplay.kindLabel(withoutSnapshot, customTypeNames: names), "Aider Renamed",
            "no snapshot falls back to the live name keyed on the full wire kind"
        )
        XCTAssertEqual(
            SessionDisplay.kindLabel(withoutSnapshot, customTypeNames: [:]), "Custom",
            "a deleted/unknown type without a snapshot renders the generic label"
        )
    }

    func testDialogLabelNeverRendersTheSessionId() {
        let sessions = [
            makeSessionResponse(id: "6f2a91c4"),
            makeSessionResponse(id: "6f2a91c4", projectName: "mobaide"),
            makeSessionResponse(id: "6f2a91c4", title: "", kind: .shell),
        ]
        for session in sessions {
            XCTAssertFalse(
                SessionDisplay.dialogLabel(session, customTypeNames: [:]).contains("6f2a91c4"),
                "a prompt asking you to destroy a session must name it in words"
            )
        }
    }

    func testAttentionMapping() {
        XCTAssertEqual(SessionDisplay.attention("awaiting_permission")?.text, "Needs permission")
        XCTAssertEqual(SessionDisplay.attention("turn_finished")?.text, "Ready")
        XCTAssertEqual(SessionDisplay.attention("turn_failed")?.text, "Failed")
        XCTAssertEqual(SessionDisplay.attention("turn_failed")?.tone, .warning)
        XCTAssertEqual(SessionDisplay.attention("ended")?.text, "Ended")
        XCTAssertNil(SessionDisplay.attention(nil))
        XCTAssertNil(SessionDisplay.attention("something_else"))
    }

    func testIsWorkingOnlyForARunningSessionTheAgentReportsMidTurn() {
        XCTAssertTrue(SessionDisplay.isWorking(makeSessionResponse(activity: "working")))
        XCTAssertFalse(SessionDisplay.isWorking(makeSessionResponse(activity: "idle")))
        XCTAssertFalse(
            SessionDisplay.isWorking(makeSessionResponse()),
            "Codex, Antigravity and Terminal sessions report no activity and keep the plain square"
        )
        for state in ["starting", "stopping", "stopped", "crashed"] {
            XCTAssertFalse(
                SessionDisplay.isWorking(makeSessionResponse(state: state, activity: "working")),
                "a \(state) session never spins, whatever its last activity"
            )
        }
    }

    func testIsWorkingYieldsToAPendingPermissionPrompt() {
        for attention in ["awaiting_permission", "awaiting_os_permission"] {
            XCTAssertFalse(
                SessionDisplay.isWorking(makeSessionResponse(attention: attention, activity: "working")),
                attention
            )
        }
    }

    func testStatusChipShowsWorkingOverAStandingReadyFromAnEarlierTurn() {
        let chip = SessionDisplay.statusChip(makeSessionResponse(attention: "turn_finished", activity: "working"))
        XCTAssertEqual(chip?.text, "Working")
        XCTAssertEqual(chip?.tone, .accent)
    }

    func testStatusChipFallsBackToTheAttentionChipOnceTheTurnIsNotWorking() {
        XCTAssertEqual(
            SessionDisplay.statusChip(makeSessionResponse(attention: "turn_finished", activity: "idle"))?.text,
            "Ready"
        )
        XCTAssertEqual(
            SessionDisplay.statusChip(makeSessionResponse(attention: "awaiting_permission", activity: "working"))?.text,
            "Needs permission"
        )
        XCTAssertNil(SessionDisplay.statusChip(makeSessionResponse(activity: "idle")))
    }

    func testIsWaitingOnlyForARunningSessionParkedOnBackgroundWork() {
        XCTAssertTrue(SessionDisplay.isWaiting(makeSessionResponse(activity: "waiting")))
        XCTAssertFalse(SessionDisplay.isWaiting(makeSessionResponse(activity: "working")))
        XCTAssertFalse(SessionDisplay.isWaiting(makeSessionResponse(activity: "idle")))
        XCTAssertFalse(
            SessionDisplay.isWaiting(makeSessionResponse(state: "crashed", activity: "waiting")),
            "a crashed session is parked on nothing, whatever its last activity"
        )
        for attention in ["awaiting_permission", "awaiting_os_permission"] {
            XCTAssertFalse(
                SessionDisplay.isWaiting(makeSessionResponse(attention: attention, activity: "waiting")),
                "\(attention): the session is blocked on the user"
            )
        }
    }

    func testStatusChipShowsBackgroundOverAStandingReadyWhileBackgroundWorkIsInFlight() {
        // "Ready" here told the user it was their move on a session that was
        // about to resume by itself.
        let chip = SessionDisplay.statusChip(makeSessionResponse(attention: "turn_finished", activity: "waiting"))
        XCTAssertEqual(chip?.text, "Background")
        XCTAssertEqual(chip?.tone, .muted)
    }

    func testStatusChipShowsNeedsPermissionOverBackground() {
        XCTAssertEqual(
            SessionDisplay.statusChip(makeSessionResponse(attention: "awaiting_permission", activity: "waiting"))?.text,
            "Needs permission"
        )
    }

    // MARK: - Active auto scans

    func testObserveScansPopulatesActiveScansFromRepository() async {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"scans":[{"project_name":"p","status":"scanning","started_at":1.5,"last_activity_at":1.5}],"max_scans":5}"#
        )
        let model = makeRESTModel()
        let observe = Task { await model.observeScans() }
        defer { observe.cancel() }

        await waitUntil { !model.activeScans.isEmpty }
        XCTAssertEqual(model.activeScans.map(\.project_name), ["p"])
    }

    // MARK: - Stop

    /// Another client got there first: the session is gone, which is the
    /// outcome the user asked for.
    func testStopTreatsA404AsAlreadyStopped() async {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Session not found"}"#)
        let model = makeRESTModel()

        await model.deleteSession("s1", force: false)

        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "DELETE")
        XCTAssertNil(model.error)
    }

    func testStopSurfacesOtherHTTPFailures() async {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 503, body: "agent offline")
        let model = makeRESTModel()

        await model.deleteSession("s1", force: false)

        XCTAssertEqual(model.error, "Failed to stop session: HTTP 503: agent offline")
    }

    // MARK: - Custom-type name map

    func testObserveCustomTypesMapsFullWireKindToName() async {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(
            status: 200,
            body: #"[{"id":"ab12cd34ef56","name":"Aider","command":"aider --model sonnet","created_at":"c","updated_at":"u"}]"#
        )
        let model = makeRESTModel()
        let observe = Task { await model.observeCustomTypes() }
        defer { observe.cancel() }

        await waitUntil { !model.customTypeNames.isEmpty }
        XCTAssertEqual(
            model.customTypeNames, ["custom_ab12cd34ef56": "Aider"],
            "the map keys on the full wire kind, never the bare 12-hex id"
        )
    }

    // MARK: - Bus-driven list + auto-resume

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

    private func sessionUpdated(_ id: String, _ state: String) -> String {
        #"{"type":"session.updated","payload":{"session":{"session_id":"\#(id)","state":"\#(state)","created_at":"t"}}}"#
    }

    func testAutoResumeWaitsForAnAgentAuthoritativeList() async throws {
        let prefs = isolatedPrefs()
        prefs.setLastUsedSessionId("a")
        let (model, repo, bus, factory) = makeStack(prefs: prefs)
        let collector = StreamCollector(model.autoResumeEvents())
        // Both observers, same as the screen: the list and the synced flag
        // settle in either order and either one can complete the decision.
        let observe = Task { await model.observeSessions() }
        let observeSynced = Task { await model.observeSessionsSynced() }
        defer {
            observe.cancel()
            observeSynced.cancel()
        }
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        // A non-empty list that is not a snapshot: it says nothing about the
        // sessions it does not mention, so absence proves nothing and neither
        // does presence.
        connection.emit(.text(sessionUpdated("a", "running")))
        let waited = await collector.expectNoElement(within: .milliseconds(300))
        XCTAssertTrue(waited, "only a full snapshot from the connected agent may arm auto-resume")

        connection.emit(.text(snapshot("a", "running")))
        let resumed = try await collector.next()
        XCTAssertEqual(resumed, "a", "the one attempt must survive the stale list, not be burned by it")
    }

    func testEndedSessionNoticeSurfacesAsAToast() async throws {
        let (model, repo, _, _) = makeStack(prefs: isolatedPrefs())
        let observe = Task { await model.observeEndedSessionNotices() }
        defer { observe.cancel() }

        repo.notifySessionEnded(id: "gone")

        await waitUntil { model.notice != nil }
        XCTAssertEqual(model.notice, "That session has ended.")
    }

    func testAutoResumeNotReplayedAfterClear() async throws {
        let prefs = isolatedPrefs()
        prefs.setLastUsedSessionId("a")
        let (model, repo, bus, factory) = makeStack(prefs: prefs)
        let observe = Task { await model.observeSessions() }
        defer { observe.cancel() }
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        connection.emit(.text(snapshot("a", "running")))

        let first = StreamCollector(model.autoResumeEvents())
        let resumed = try await first.next()
        XCTAssertEqual(resumed, "a")
        model.clearAutoResume()

        let second = StreamCollector(model.autoResumeEvents())
        let noReplay = await second.expectNoElement(within: .milliseconds(200))
        XCTAssertTrue(noReplay, "a cleared auto-resume id must not replay to a returning list subscriber")
    }
}
