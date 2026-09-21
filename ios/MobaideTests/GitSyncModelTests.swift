import XCTest
@testable import Mobaide

/// The fetch / pull / push half of `GitChangesModel` over the real `AgentAPI`
/// (stubbed transport) and a real `AgentEventBus` over the fake socket: what
/// each start sends, how a job is adopted from the answer, a `409` or a
/// socket frame, the 3 s reconcile loop and the 6 s auto-dismiss, both driven
/// through `ManualSleeper.tick()`.
@MainActor
final class GitSyncModelTests: XCTestCase {
    @MainActor
    private final class Flag {
        var raised = false
    }

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeModel(
        sleeper: any Sleeper = ManualSleeper(),
        openSync: Bool = false
    ) -> (GitChangesModel, AgentEventBus, FakeWebSocketTransportFactory) {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let factory = FakeWebSocketTransportFactory()
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: client
        )
        let model = GitChangesModel(
            sessionId: "s1",
            agentAPI: agentAPI,
            agentEventBus: bus,
            projectName: { "proj" },
            sleeper: sleeper,
            openSync: openSync
        )
        return (model, bus, factory)
    }

    private let allGit = #"{"changes":true,"stage":true,"discard":true,"commit":true,"fetch":true,"pull":true,"push":true}"#

    /// The full fixture on screen with every git capability, the probe's and
    /// the load's traffic already forgotten.
    private func loadedModel(
        sleeper: any Sleeper = ManualSleeper()
    ) async throws -> (GitChangesModel, AgentEventBus, FakeWebSocketTransportFactory) {
        let (model, bus, factory) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"name":"mobaide-agent","server_version":"1.4.0","protocol_versions":[7],"min_client_version":1,"capabilities":{"git":\#(allGit)}}"#
        )
        await model.loadCapabilities()
        StubURLProtocol.enqueue(status: 200, body: try statusBody("linked_worktree_full"))
        await model.load()
        StubURLProtocol.reset()
        return (model, bus, factory)
    }

    private func statusBody(_ name: String) throws -> String {
        String(decoding: try FixtureLoader.wireData(path: "dto/agent/GitStatusResponse.json", case: name), as: UTF8.self)
    }

    private func jobBody(_ name: String) throws -> String {
        String(decoding: try FixtureLoader.wireData(path: "dto/agent/GitJobResponse.json", case: name), as: UTF8.self)
    }

    /// The id every `running_*` / `done_ok` / `done_rejected` golden case carries.
    private let fixtureJobId = "3f9c2b7e1d4a4c0f9b8e7d6c5a4b3c2d"

    private func job(
        id: String,
        op: String = GitJobResponse.opPush,
        status: String = GitJobResponse.statusRunning,
        percent: Int? = nil,
        ok: Bool? = nil,
        errorCode: String? = nil,
        message: String? = nil
    ) -> String {
        let phase = percent == nil ? "null" : #""Writing objects""#
        let pct = percent.map(String.init) ?? "null"
        let okText = ok.map { $0 ? "true" : "false" } ?? "null"
        let code = errorCode.map { #""\#($0)""# } ?? "null"
        let msg = message.map { #""\#($0)""# } ?? "null"
        return #"{"job_id":"\#(id)","session_id":"s1","op":"\#(op)","status":"\#(status)","phase":\#(phase),"percent":\#(pct),"line":null,"ok":\#(okText),"error_code":\#(code),"message":\#(msg)}"#
    }

    private func frame(_ type: String, _ payload: String) -> String {
        #"{"type":"\#(type)","payload":\#(payload)}"#
    }

    private var requestURLs: [String] {
        StubURLProtocol.requests.compactMap { $0.url?.absoluteString }
    }

    private func gitURL(_ leaf: String) -> String { "http://agent.example.com/api/sessions/s1/git/\(leaf)" }

    private func sentBody<T: Decodable>(_ type: T.Type, at index: Int) throws -> T {
        try JSONDecoder().decode(type, from: try XCTUnwrap(StubURLProtocol.bodies[safe: index]))
    }

    /// The socket the frames arrive on, opened, with the model listening.
    private func openFrames(
        _ model: GitChangesModel, _ bus: AgentEventBus, _ factory: FakeWebSocketTransportFactory
    ) async throws -> FakeWebSocketConnection {
        Task { await model.observeGitJobFrames() }
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        return connection
    }

    // MARK: - Starting

    func testStartSyncPostsEachOpWithItsBodyAndFollowsTheAnswer() async throws {
        let (model, _, _) = try await loadedModel()
        StubURLProtocol.enqueue(status: 200, body: try jobBody("running_start"))
        model.openSync()

        await model.startSync(op: GitJobResponse.opPush)

        XCTAssertEqual(requestURLs, [gitURL("push")])
        XCTAssertEqual(StubURLProtocol.requests[0].httpMethod, "POST")
        XCTAssertEqual(try sentBody(GitPushRequest.self, at: 0), GitPushRequest(force: false))
        XCTAssertEqual(model.syncSheet, SyncSheetState(), "the sheet is back to idle, without an error")
        XCTAssertEqual(model.job?.jobId, fixtureJobId)
        XCTAssertEqual(model.job?.running, true)
        XCTAssertTrue(model.isBusy, "a running job locks the staged set")

        model.dismissJobResult()
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: job(id: "j2", op: GitJobResponse.opPull))
        await model.startSync(op: GitJobResponse.opPull)
        XCTAssertEqual(requestURLs, [gitURL("pull")])
        XCTAssertEqual(try sentBody(GitPullRequest.self, at: 0), GitPullRequest(mode: GitPullRequest.modeFFOnly))

        model.dismissJobResult()
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: job(id: "j3", op: GitJobResponse.opFetch))
        await model.startSync(op: GitJobResponse.opFetch)
        XCTAssertEqual(requestURLs, [gitURL("fetch")])
        XCTAssertEqual(StubURLProtocol.bodies[safe: 0]?.isEmpty, true, "fetch carries no body")
        XCTAssertEqual(model.job?.jobId, "j3")
    }

    func testFollowUpsGoThroughThePromptAndEachSendsItsOwnRequest() async throws {
        let (model, _, _) = try await loadedModel()
        model.openSync()

        model.requestFollowUp(.forcePush)
        XCTAssertEqual(model.syncPrompt?.title, "Force push?")
        XCTAssertEqual(model.syncPrompt?.body, "Overwrites origin/feature/x with your branch. Proceeds only if the remote hasn't moved since your last fetch.")
        model.dismissFollowUp()
        XCTAssertNil(model.syncPrompt)
        await model.confirmFollowUp()
        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "a dismissed prompt sends nothing")

        StubURLProtocol.enqueue(status: 200, body: job(id: "j1"))
        model.requestFollowUp(.forcePush)
        await model.confirmFollowUp()
        XCTAssertNil(model.syncPrompt)
        XCTAssertEqual(requestURLs, [gitURL("push")])
        XCTAssertEqual(try sentBody(GitPushRequest.self, at: 0), GitPushRequest(force: true))

        model.dismissJobResult()
        StubURLProtocol.enqueue(status: 200, body: job(id: "j2", op: GitJobResponse.opPull))
        model.requestFollowUp(.pullRebase)
        await model.confirmFollowUp()
        XCTAssertEqual(try sentBody(GitPullRequest.self, at: 1), GitPullRequest(mode: GitPullRequest.modeRebase))

        model.dismissJobResult()
        StubURLProtocol.enqueue(status: 200, body: job(id: "j3", op: GitJobResponse.opPull))
        model.requestFollowUp(.pullMerge)
        await model.confirmFollowUp()
        XCTAssertEqual(try sentBody(GitPullRequest.self, at: 2), GitPullRequest(mode: GitPullRequest.modeMerge))
    }

    func testAJobRunningRefusalAdoptsTheJobItNamesAndReportsInTheSheet() async throws {
        let (model, _, _) = try await loadedModel()
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"job_running","message":"j9"}}"#)
        model.openSync()

        await model.startSync(op: GitJobResponse.opFetch)

        XCTAssertEqual(model.syncSheet, SyncSheetState(isStarting: false, error: "Another fetch, pull or push is still running."))
        XCTAssertEqual(model.job, GitJobBanner(jobId: "j9", job: nil))
        XCTAssertTrue(model.isBusy, "the adopted job is running until a snapshot says otherwise")
    }

    func testAStartFailureWithTheSheetClosedIsAToast() async throws {
        let (model, _, _) = try await loadedModel()
        StubURLProtocol.enqueue(status: 503, body: #"{"detail":{"code":"git_unavailable","message":null}}"#)

        await model.startSync(op: GitJobResponse.opFetch)

        XCTAssertNil(model.syncSheet)
        XCTAssertNil(model.job)
        XCTAssertEqual(model.toast?.text, "git is not installed on the agent host.")
        XCTAssertEqual(model.toast?.tone, .danger)
    }

    func testCloseSyncDropsTheSheetAndConsumeOpenSyncOpensItOnce() async throws {
        let (model, _, _) = makeModel(openSync: true)
        XCTAssertTrue(model.openSyncOnAppear)
        XCTAssertNil(model.syncSheet)

        model.consumeOpenSync()

        XCTAssertEqual(model.syncSheet, SyncSheetState())
        XCTAssertFalse(model.openSyncOnAppear)

        model.closeSync()
        XCTAssertNil(model.syncSheet)
        model.consumeOpenSync()
        XCTAssertNil(model.syncSheet, "the terminal menu's Fetch / Pull / Push… opens the sheet once")

        let (plain, _, _) = makeModel()
        plain.consumeOpenSync()
        XCTAssertNil(plain.syncSheet)
    }

    // MARK: - Frames

    func testAFrameForAnUnknownJobIsAdoptedAndADismissedOneIsNot() async throws {
        let (model, bus, factory) = try await loadedModel()
        let connection = try await openFrames(model, bus, factory)

        connection.emit(.text(frame("git.job.progress", job(id: "j5", op: GitJobResponse.opPull, percent: 12))))
        await waitUntil { model.job != nil }

        XCTAssertEqual(model.job?.jobId, "j5")
        XCTAssertEqual(model.job?.job?.percent, 12)
        XCTAssertTrue(model.isBusy)

        model.dismissJobResult()
        XCTAssertNil(model.job)
        XCTAssertFalse(model.isBusy)

        connection.emit(.text(frame("git.job.progress", job(id: "j5", op: GitJobResponse.opPull, percent: 50))))
        connection.emit(.text(frame("git.job.progress", job(id: "j6", op: GitJobResponse.opFetch))))
        await waitUntil { model.job != nil }

        XCTAssertEqual(model.job?.jobId, "j6", "the dismissed job stays dismissed; a new one is adopted")
    }

    func testAFrameForAnotherSessionIsIgnored() async throws {
        let (model, bus, factory) = try await loadedModel()
        let connection = try await openFrames(model, bus, factory)

        connection.emit(.text(frame("git.job.progress", job(id: "j5").replacingOccurrences(of: #""session_id":"s1""#, with: #""session_id":"s2""#))))
        connection.emit(.text(frame("git.job.progress", job(id: "j7"))))
        await waitUntil { model.job != nil }

        XCTAssertEqual(model.job?.jobId, "j7", "processed in order: had s2's frame been adopted, j5 would show")
    }

    func testFramesForTheFollowedJobApplyMonotonically() async throws {
        let (model, bus, factory) = try await loadedModel()
        let connection = try await openFrames(model, bus, factory)
        connection.emit(.text(frame("git.job.progress", job(id: "j1", percent: 10))))
        await waitUntil { model.job?.job?.percent == 10 }

        connection.emit(.text(frame("git.job.progress", job(id: "j1", percent: 40))))
        await waitUntil { model.job?.job?.percent == 40 }
        connection.emit(.text(frame("git.job.done", job(id: "j1", status: GitJobResponse.statusDone, ok: false, errorCode: "rejected", message: "! [rejected]"))))
        await waitUntil { model.job?.running == false }
        XCTAssertEqual(model.job?.job?.error_code, "rejected")
        XCTAssertFalse(model.isBusy)

        // A progress frame the relay delivered late: the outcome must survive it.
        connection.emit(.text(frame("git.job.progress", job(id: "j1", percent: 99))))
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(model.job?.running, false, "a running snapshot never overwrites a done one")
        XCTAssertEqual(model.job?.job?.error_code, "rejected")
        XCTAssertNotEqual(model.job?.job?.percent, 99)
    }

    // MARK: - Reconcile loop

    func testRunJobReconcilePollsAtOnceThenEveryThreeSecondsUntilDone() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = try await loadedModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: try jobBody("running_start"))
        await model.startSync(op: GitJobResponse.opPush)
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: try jobBody("running_progress"))
        let done = Flag()

        Task {
            await model.runJobReconcile()
            done.raised = true
        }
        await waitUntil { sleeper.isWaiting }

        XCTAssertEqual(requestURLs, [gitURL("jobs/\(fixtureJobId)")], "the first poll is immediate")
        XCTAssertEqual(StubURLProtocol.requests[0].httpMethod, "GET")
        XCTAssertEqual(model.job?.job?.percent, 45)
        XCTAssertEqual(sleeper.durations, [GitChangesModel.jobPollInterval])
        XCTAssertEqual(GitChangesModel.jobPollInterval, .seconds(3))

        StubURLProtocol.enqueue(status: 200, body: try jobBody("done_ok"))
        sleeper.tick()
        await waitUntil { done.raised }

        XCTAssertTrue(done.raised, "the loop ends on its own once the job is done")
        XCTAssertEqual(StubURLProtocol.requests.count, 2)
        XCTAssertEqual(model.job?.running, false)
        XCTAssertEqual(model.job?.job?.message, "443f30d..9419146 main -> main")
        XCTAssertFalse(model.isBusy)
    }

    func testASuccessfulJobClearsItsBannerAfterSixSecondsAFailedOneStays() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = try await loadedModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: try jobBody("running_start"))
        await model.startSync(op: GitJobResponse.opPush)
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: try jobBody("done_ok"))

        await model.runJobReconcile()
        await waitUntil { sleeper.isWaiting }

        XCTAssertEqual(model.job?.running, false)
        XCTAssertEqual(sleeper.durations, [GitChangesModel.jobResultDismissDelay], "no poll sleep: the dismiss timer is the only one armed")
        XCTAssertEqual(GitChangesModel.jobResultDismissDelay, .seconds(6))

        sleeper.tick()
        await waitUntil { model.job == nil }
        XCTAssertNil(model.job)

        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: job(id: "j2"))
        await model.startSync(op: GitJobResponse.opPush)
        StubURLProtocol.enqueue(status: 200, body: try jobBody("done_rejected").replacingOccurrences(of: "a1b2c3d4e5f60718293a4b5c6d7e8f90", with: "j2"))
        await model.runJobReconcile()
        try await Task.sleep(for: .milliseconds(20))

        XCTAssertEqual(model.job?.job?.error_code, "rejected")
        XCTAssertEqual(sleeper.durations.count, 1, "a failure arms no dismiss timer; it stays until dismissed")
        XCTAssertFalse(model.isBusy)
    }

    func testJobNotFoundMarksTheJobLostEndsTheLoopAndFreesTheWrites() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = try await loadedModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: try jobBody("running_start"))
        await model.startSync(op: GitJobResponse.opPush)
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":{"code":"job_not_found"}}"#)

        await model.runJobReconcile()

        XCTAssertEqual(model.job?.lost, true)
        XCTAssertEqual(model.job?.text(), "Lost track of the job — refresh to see the result.")
        XCTAssertEqual(model.job?.failed, true)
        XCTAssertFalse(model.isBusy)
        XCTAssertTrue(sleeper.durations.isEmpty, "nothing left to poll")
        XCTAssertNotEqual(model.state, .agentOutdated, "a coded 404 is the job's answer, not an old agent")
    }

    func testAGoneSessionOrAnOldAgentOnThePollMarksTheJobLostAndEndsTheLoop() async throws {
        for body in [#"{"detail":"Session 's1' not found"}"#, #"{"detail":"Not Found"}"#] {
            let sleeper = ManualSleeper()
            let (model, _, _) = try await loadedModel(sleeper: sleeper)
            StubURLProtocol.enqueue(status: 200, body: try jobBody("running_start"))
            await model.startSync(op: GitJobResponse.opPush)
            StubURLProtocol.reset()
            StubURLProtocol.enqueue(status: 404, body: body)

            await model.runJobReconcile()

            XCTAssertEqual(model.job?.lost, true, body)
            XCTAssertFalse(model.isBusy, body)
            XCTAssertTrue(sleeper.durations.isEmpty, "nothing left to poll after \(body)")
            StubURLProtocol.reset()
        }
    }

    func testATransientPollFailureKeepsTheSnapshotAndKeepsPolling() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = try await loadedModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: try jobBody("running_progress"))
        await model.startSync(op: GitJobResponse.opPush)
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 503, body: #"{"detail":"Agent is offline"}"#)

        Task { await model.runJobReconcile() }
        await waitUntil { sleeper.isWaiting }

        XCTAssertEqual(model.job?.job?.percent, 45, "the failed poll leaves the last snapshot")
        XCTAssertEqual(model.job?.lost, false)
        XCTAssertEqual(sleeper.durations, [GitChangesModel.jobPollInterval], "and the loop goes on")
    }

    func testCancelSyncPostsCancelAndAppliesTheAnswer() async throws {
        let (model, _, _) = try await loadedModel()
        await model.cancelSync()
        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "nothing to cancel without a job")

        StubURLProtocol.enqueue(status: 200, body: try jobBody("running_start"))
        await model.startSync(op: GitJobResponse.opPush)
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(
            status: 200,
            body: job(id: fixtureJobId, status: GitJobResponse.statusDone, ok: false, errorCode: "cancelled", message: "cancelled")
        )

        await model.cancelSync()

        XCTAssertEqual(requestURLs, [gitURL("jobs/\(fixtureJobId)/cancel")])
        XCTAssertEqual(StubURLProtocol.requests[0].httpMethod, "POST")
        XCTAssertEqual(model.job?.running, false)
        XCTAssertEqual(model.job?.job?.error_code, "cancelled")
        XCTAssertEqual(model.job?.text(), "Push failed: Cancelled.")
        XCTAssertFalse(model.isBusy)
    }

    func testJobReconcileKeyChangesWithTheJobItsStateAndTheForeground() async throws {
        let (model, _, _) = try await loadedModel()
        let idle = model.jobReconcileKey(active: true)

        StubURLProtocol.enqueue(status: 200, body: try jobBody("running_start"))
        await model.startSync(op: GitJobResponse.opPush)
        let running = model.jobReconcileKey(active: true)
        let background = model.jobReconcileKey(active: false)

        XCTAssertNotEqual(idle, running)
        XCTAssertNotEqual(running, background)
        XCTAssertEqual(running, model.jobReconcileKey(active: true), "stable while nothing changed")
    }
}

private extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}
