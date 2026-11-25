import XCTest
@testable import Mobaide

@MainActor
final class AutoScanDetailModelTests: XCTestCase {
    private struct StubAgentTokens: AgentTokenProviding {
        func validToken() async -> AgentToken? { nil }
        func refreshedToken() async -> String? { nil }
    }

    private func makeModel(workspaceName: String = "p", agentAPI: AgentAPI? = nil) -> AutoScanDetailModel {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: .live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = agentAPI ?? AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
        let wsClient = AutoScanWsClient(
            webSocketFactory: .live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            urlProvider: { nil },
            isAuthenticated: { false }
        )
        let repo = AutoScanRepository(agentAPI: agentAPI, agentEventBus: bus, wsClient: wsClient)
        return AutoScanDetailModel(workspaceName: workspaceName, repository: repo)
    }

    private func phaseFrame(seq: Int64, phase: String, cycle: Int = 1, next: String? = nil, issues: Int = 0, workspace: String = "p") -> AutoScanFrame {
        .event(ParsedScanEvent(workspaceName: workspace, seq: seq, ts: "T", type: "phase",
                               payload: .phase(phase: phase, cycleNumber: cycle, nextScanAt: next, issuesProcessed: issues)))
    }

    private func issueFrame(seq: Int64, id: String, step: String = "state_update", status: String = "started", workspace: String = "p") -> AutoScanFrame {
        .event(ParsedScanEvent(workspaceName: workspace, seq: seq, ts: "T", type: "issue",
                               payload: .issue(identifier: id, title: "t", step: step, status: status, error: nil)))
    }

    private func logFrame(seq: Int64, line: String, ansi: Bool = false, workspace: String = "p") -> AutoScanFrame {
        .event(ParsedScanEvent(workspaceName: workspace, seq: seq, ts: "T", type: "log",
                               payload: .log(line: line, level: "info", ansi: ansi)))
    }

    private func instance(cycle: Int = 0, next: String? = nil) -> AutoScanInstanceDto {
        AutoScanInstanceDto(workspace_name: "p", status: "scanning", started_at: 1.5, last_activity_at: 1.5,
                            next_scan_at: next, cycle_number: cycle)
    }

    // MARK: - Frame dispatch

    func testSnapshotSeedsHeaderAndReducesBufferedEvents() async {
        let model = makeModel()
        await model.handle(.snapshot(scans: [instance(cycle: 5)], events: ["p": [phaseFrame(seq: 10, phase: "scan_started", cycle: 5).parsedEvent]]))
        XCTAssertEqual(model.connection, .replaying)
        XCTAssertEqual(model.instance?.workspace_name, "p")
        XCTAssertEqual(model.cycleNumber, 5)
        XCTAssertEqual(model.activePhase, "scan")
    }

    func testASnapshotWithoutTheScanShowsItStopped() async {
        let model = makeModel()
        // The PC no longer runs the scan: no event will come to hide the controls.
        await model.handle(.snapshot(scans: [], events: [:]))
        XCTAssertTrue(model.stopped)
        XCTAssertNil(model.nextScanAt)
    }

    func testEventForDifferentWorkspaceIsIgnored() async {
        let model = makeModel()
        await model.handle(phaseFrame(seq: 1, phase: "scan_started", workspace: "other"))
        XCTAssertNil(model.activePhase)
        XCTAssertTrue(model.logLines.isEmpty)
    }

    // MARK: - Phase timeline

    func testScanCompletedIdleMarksPendingPhasesSkipped() async {
        let model = makeModel()
        await model.handle(phaseFrame(seq: 1, phase: "scan_started", cycle: 1))
        await model.handle(phaseFrame(seq: 2, phase: "scan_completed", cycle: 1, next: "T2", issues: 0))
        XCTAssertNil(model.activePhase)
        XCTAssertEqual(model.phases["scan"]?.status, "ok")
        XCTAssertEqual(model.phases["state"]?.status, "skipped")
        XCTAssertEqual(model.phases["label"]?.status, "skipped")
        XCTAssertEqual(model.nextScanAt, "T2")
    }

    func testScanFailedTakesTheNextScanAtAndFailsTheActivePhase() async {
        let model = makeModel()
        await model.handle(phaseFrame(seq: 1, phase: "scan_started", cycle: 1))
        await model.handle(phaseFrame(seq: 2, phase: "scan_failed", cycle: 1, next: "T3"))
        XCTAssertEqual(model.nextScanAt, "T3")
        XCTAssertNil(model.activePhase)
        XCTAssertEqual(model.phases["scan"]?.status, "failed")
        XCTAssertEqual(model.phases["state"]?.status, "pending")
        XCTAssertFalse(model.stopped)
    }

    func testScanCompletedNonIdleKeepsPendingPhases() async {
        let model = makeModel()
        await model.handle(phaseFrame(seq: 1, phase: "scan_started", cycle: 1))
        await model.handle(phaseFrame(seq: 2, phase: "scan_completed", cycle: 1, issues: 3))
        XCTAssertEqual(model.phases["scan"]?.status, "ok")
        XCTAssertEqual(model.phases["state"]?.status, "pending")
    }

    func testIssueStartedDerivesActivePhaseFromStep() async {
        let model = makeModel()
        await model.handle(issueFrame(seq: 1, id: "ISS-1", step: "worktree_create", status: "started"))
        XCTAssertEqual(model.activePhase, "worktree")
        XCTAssertEqual(model.phases["worktree"]?.status, "active")
    }

    func testRecentIssuesCappedAt50DroppingOldest() async {
        let model = makeModel()
        for i in 0..<55 {
            await model.handle(issueFrame(seq: Int64(i + 1), id: "ISSUE-\(i)"))
        }
        XCTAssertEqual(model.recentIssues.count, 50)
        XCTAssertEqual(model.recentIssues.first?.identifier, "ISSUE-5")
        XCTAssertEqual(model.recentIssues.last?.identifier, "ISSUE-54")
    }

    // MARK: - Log ring

    func testLogRingCappedAt2000WithMonotonicIds() async {
        let model = makeModel()
        for i in 1...(AutoScanDetailModel.logRingMax + 1) {
            await model.handle(logFrame(seq: Int64(i), line: "log-\(i)"))
        }
        XCTAssertEqual(model.logLines.count, AutoScanDetailModel.logRingMax)
        XCTAssertEqual(model.logLines.first?.id, 1)
        XCTAssertEqual(model.logLines.last?.id, Int64(AutoScanDetailModel.logRingMax))
        let ids = model.logLines.map(\.id)
        XCTAssertTrue(zip(ids, ids.dropFirst()).allSatisfy { $0 < $1 }, "log ids must be strictly increasing / never recycled")
    }

    func testIdleScanCompletedAppendsTwoLogLinesFromOneEvent() async {
        let model = makeModel()
        await model.handle(phaseFrame(seq: 1, phase: "scan_completed", cycle: 3, issues: 0))
        XCTAssertEqual(model.logLines.count, 2)
    }

    // MARK: - Idempotency (the fix)

    func testReplayingSameStreamTwiceYieldsIdenticalState() async {
        let frames: [AutoScanFrame] = [
            phaseFrame(seq: 1, phase: "scan_started", cycle: 4),
            issueFrame(seq: 2, id: "ISS-1", step: "state_update", status: "ok"),
            logFrame(seq: 3, line: "hello"),
            phaseFrame(seq: 4, phase: "scan_completed", cycle: 4, next: "T9", issues: 1),
        ]
        let first = makeModel()
        for frame in frames { await first.handle(frame) }
        let both = makeModel()
        for frame in frames { await both.handle(frame) }
        for frame in frames { await both.handle(frame) }

        XCTAssertEqual(both.logLines, first.logLines)
        XCTAssertEqual(both.recentIssues, first.recentIssues)
        XCTAssertEqual(both.phases, first.phases)
        XCTAssertEqual(both.cycleNumber, first.cycleNumber)
        XCTAssertEqual(both.nextScanAt, first.nextScanAt)
    }

    func testDuplicateSeqReducesOnce() async {
        let model = makeModel()
        await model.handle(logFrame(seq: 5, line: "once"))
        await model.handle(logFrame(seq: 5, line: "duplicate"))
        XCTAssertEqual(model.logLines.count, 1)
    }

    func testOutOfOrderLowerSeqIsSkipped() async {
        let model = makeModel()
        await model.handle(logFrame(seq: 10, line: "a"))
        await model.handle(logFrame(seq: 5, line: "b"))
        XCTAssertEqual(model.logLines.count, 1)
    }

    // MARK: - Autoscroll pause

    func testPausedAccumulatesNewLineCountThenResumeResets() async {
        let model = makeModel()
        model.setPaused(true)
        for i in 1...3 {
            await model.handle(logFrame(seq: Int64(i), line: "l\(i)"))
        }
        XCTAssertEqual(model.newLineCount, 3)
        model.resumeAutoscroll()
        XCTAssertFalse(model.isPausedAutoscroll)
        XCTAssertEqual(model.newLineCount, 0)
    }

    // MARK: - Stop

    /// The PC answers `POST /api/autoscan/stop` with `stopAnswer`.
    private func runningModelStopped(withAnswer stopAnswer: Int) async -> AutoScanDetailModel {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: stopAnswer, body: #"{"detail":"x"}"#)
        let model = makeModel(
            agentAPI: AgentAPI(
                baseURL: { "http://agent.example.com" },
                tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
                client: HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
            )
        )
        await model.handle(.snapshot(scans: [instance(cycle: 2, next: "T1")], events: [:]))
        await model.stopScan()
        return model
    }

    func testASuccessfulStopShowsStoppedUntilASnapshotListsTheScanAgain() async {
        let model = await runningModelStopped(withAnswer: 200)

        // The agent no longer lists the scan, so no event would update the header.
        XCTAssertTrue(model.stopped)
        XCTAssertEqual(model.statusText, "stopped")
        XCTAssertNil(model.nextScanAt)

        // A reconnect while the scan is still gone keeps it stopped.
        await model.handle(.snapshot(scans: [], events: [:]))
        XCTAssertTrue(model.stopped)

        // The scan was started again elsewhere.
        await model.handle(.snapshot(scans: [instance(cycle: 3)], events: [:]))
        XCTAssertFalse(model.stopped)
        XCTAssertEqual(model.statusText, "scanning")
    }

    func testAStopThePCRefusesWith404ShowsStopped() async {
        let model = await runningModelStopped(withAnswer: 404)

        // The PC no longer runs the scan, so Run now and Stop must go.
        XCTAssertTrue(model.stopped)
        XCTAssertNil(model.nextScanAt)
    }

    func testAFailedStopKeepsTheScanControls() async {
        let model = await runningModelStopped(withAnswer: 500)

        XCTAssertFalse(model.stopped)
        XCTAssertEqual(model.nextScanAt, "T1")
    }

    // MARK: - Follow a scan started again elsewhere

    private struct LiveStack {
        let model: AutoScanDetailModel
        let repository: AutoScanRepository
        let sockets: FakeWebSocketTransportFactory
        let runTask: Task<Void, Never>
    }

    /// `run()` over a scripted `ws/autoscan` socket, with the list seeded over REST.
    private func liveModel(listedRun: Double) async throws -> LiveStack {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: listBody(startedAt: listedRun))
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: .live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        )
        let sockets = FakeWebSocketTransportFactory()
        let wsClient = AutoScanWsClient(
            webSocketFactory: .live(transportFactory: sockets, sleeper: RecordingSleeper()),
            urlProvider: { "ws://host/ws/autoscan?token=t" },
            isAuthenticated: { true }
        )
        let repo = AutoScanRepository(agentAPI: agentAPI, agentEventBus: bus, wsClient: wsClient)
        _ = await repo.refresh()
        let model = AutoScanDetailModel(workspaceName: "p", repository: repo)
        let runTask = Task { await model.run() }

        let socket = try await sockets.connection(1)
        socket.emit(.opened)
        socket.emit(.text(#"{"type":"snapshot","data":{"scans":[\#(scanJSON(startedAt: listedRun))],"events":{}}}"#))
        socket.emit(.text(wsEvent(seq: 5, type: "log", payload: #"{"line":"old run","level":"info","ansi":false}"#)))
        await waitUntil { model.logLines.count == 1 }
        XCTAssertEqual(model.logLines.count, 1)
        return LiveStack(model: model, repository: repo, sockets: sockets, runTask: runTask)
    }

    private func scanJSON(startedAt: Double) -> String {
        #"{"project_name":"p","status":"scanning","started_at":\#(startedAt),"last_activity_at":\#(startedAt)}"#
    }

    private func listBody(startedAt: Double) -> String {
        #"{"scans":[\#(scanJSON(startedAt: startedAt))],"max_scans":5}"#
    }

    private func wsEvent(seq: Int64, type: String, payload: String) -> String {
        #"{"type":"event","project_name":"p","event":{"seq":\#(seq),"ts":"T","type":"\#(type)","payload":\#(payload)}}"#
    }

    private func resumeMap(_ sent: String?) -> [String: Any]? {
        guard let sent, let object = try? JSONSerialization.jsonObject(with: Data(sent.utf8)) as? [String: Any] else { return nil }
        return object["resume_from_seq"] as? [String: Any]
    }

    func testAScanStoppedElsewhereAndListedWithANewRunRestartsTheStreamFromAFreshState() async throws {
        let stack = try await liveModel(listedRun: 1.5)
        defer { stack.runTask.cancel() }
        let model = stack.model
        let first = try await stack.sockets.connection(1)
        first.emit(.text(wsEvent(seq: 6, type: "phase", payload: #"{"phase":"scan_stopped","cycle_number":1}"#)))
        await waitUntil { model.stopped }
        XCTAssertTrue(model.stopped)

        // Started again elsewhere: a new run's seq starts at 1, so the cursor must not carry over.
        StubURLProtocol.enqueue(status: 200, body: listBody(startedAt: 2.5))
        _ = await stack.repository.refresh()
        let second = try await stack.sockets.connection(2)
        XCTAssertFalse(model.stopped)
        XCTAssertTrue(model.logLines.isEmpty)
        XCTAssertNil(model.instance)
        XCTAssertTrue(model.phases.values.allSatisfy { $0.status == "pending" })

        second.emit(.opened)
        await waitUntil { !second.sentTexts.isEmpty }
        let resume = try XCTUnwrap(resumeMap(second.sentTexts.first))
        XCTAssertTrue(resume.isEmpty, "a restarted detail must not resume from the old run's cursor")

        // The reopened stream feeds the fresh state.
        second.emit(.text(wsEvent(seq: 1, type: "log", payload: #"{"line":"new run","level":"info","ansi":false}"#)))
        await waitUntil { model.logLines.count == 1 }
        XCTAssertEqual(model.logLines.count, 1)
    }

    func testAStopMadeOnTheScreenDoesNotRestartTheStream() async throws {
        let stack = try await liveModel(listedRun: 1.5)
        defer { stack.runTask.cancel() }
        let model = stack.model

        StubURLProtocol.enqueue(status: 200, body: "{}")
        await model.stopScan()
        XCTAssertTrue(model.stopped)
        // A late list push that still names the stopped run is not a new run either.
        StubURLProtocol.enqueue(status: 200, body: listBody(startedAt: 1.5))
        _ = await stack.repository.refresh()

        let noNewSocket = await stack.sockets.assertNoConnection(beyond: 1)
        XCTAssertTrue(noNewSocket)
        XCTAssertTrue(model.stopped)
        XCTAssertEqual(model.logLines.count, 1)
    }
}

private extension AutoScanFrame {
    var parsedEvent: ParsedScanEvent {
        guard case .event(let event) = self else { fatalError("not an event frame") }
        return event
    }
}
