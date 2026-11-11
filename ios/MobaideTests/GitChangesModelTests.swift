import XCTest
@testable import Mobaide

/// The session-scoped Changes model over the real `AgentAPI` (stubbed
/// transport) and a real `AgentEventBus` over the fake socket. Bodies are the
/// golden `GitStatusResponse` fixtures; the poll is driven one iteration at a
/// time through `ManualSleeper.tick()`.
@MainActor
final class GitChangesModelTests: XCTestCase {
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
        projectName: String? = "proj",
        openCommit: Bool = false
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
            projectName: { projectName },
            sleeper: sleeper,
            openCommit: openCommit
        )
        return (model, bus, factory)
    }

    private func fixtureBody(_ name: String) throws -> String {
        String(decoding: try FixtureLoader.wireData(path: "dto/agent/GitStatusResponse.json", case: name), as: UTF8.self)
    }

    private var requestURLs: [String] {
        StubURLProtocol.requests.compactMap { $0.url?.absoluteString }
    }

    private let statusURL = "http://agent.example.com/api/sessions/s1/git/status"
    private let fullEtag = "3f0d4c1e9a7b2c5d6e8f9a0b1c2d3e4f5a6b7c8d"

    private func readyStatus(_ model: GitChangesModel) -> GitStatusResponse? {
        guard case .ready(let status, _, _) = model.state else { return nil }
        return status
    }

    // MARK: - load()

    func testFirstLoadCarriesNoEtagAndPublishesReady() async throws {
        let (model, _, _) = makeModel()
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("linked_worktree_full"))

        await model.load()

        XCTAssertEqual(requestURLs, [statusURL])
        guard case .ready(let status, let repo, let sections) = model.state else {
            return XCTFail("expected ready, got \(model.state)")
        }
        XCTAssertEqual(status.etag, fullEtag)
        XCTAssertEqual(repo.toplevel, "/home/user/proj/.wt/feature-x")
        XCTAssertEqual(sections.map(\.group), ["conflicts", "staged", "unstaged", "untracked"])
        XCTAssertFalse(model.isLoading)
        XCTAssertNil(model.staleError)
    }

    func testSecondLoadSendsTheEtagAndUnchangedKeepsTheCachedBody() async throws {
        let (model, _, _) = makeModel()
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("linked_worktree_full"))
        await model.load()
        let first = try XCTUnwrap(readyStatus(model))

        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))
        await model.load()

        XCTAssertEqual(requestURLs, [statusURL, "\(statusURL)?etag=\(fullEtag)"])
        XCTAssertEqual(readyStatus(model), first, "the unchanged shell must not replace the rows")
    }

    func testUnchangedWithNothingCachedRefetchesWithoutAnEtag() async throws {
        // A stale-cache answer on a cold model (the agent's etag matched a
        // body this instance never saw) is a shell with no rows in it.
        let (model, _, _) = makeModel()
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("linked_worktree_full"))

        await model.load()

        XCTAssertEqual(requestURLs, [statusURL, statusURL])
        XCTAssertEqual(readyStatus(model)?.etag, fullEtag)
    }

    func testUnavailableReasonBecomesTheUnavailableState() async throws {
        let (model, _, _) = makeModel()
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("not_a_repo"))

        await model.load()

        XCTAssertEqual(model.state, .unavailable(reason: "not_a_repo", cwd: "/home/user/plain"))
    }

    func testFailureWithNothingCachedIsAnErrorStateAndRetryRecovers() async throws {
        let (model, _, _) = makeModel()
        StubURLProtocol.enqueue(status: 500, body: #"{"detail":{"code":"git_error","message":"boom"}}"#)
        await model.load()
        XCTAssertEqual(model.state, .error("git failed: boom"))
        XCTAssertNil(model.staleError)

        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("detached_head"))
        await model.load()
        XCTAssertEqual(readyStatus(model)?.session_id, "s2")
    }

    func testFailedRefreshOverCachedContentIsAStaleBannerNotAStateChange() async throws {
        let (model, _, _) = makeModel()
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("linked_worktree_full"))
        await model.load()
        let cached = try XCTUnwrap(readyStatus(model))

        StubURLProtocol.enqueue(status: 503, body: #"{"detail":{"code":"git_unavailable","message":null}}"#)
        await model.load()
        XCTAssertEqual(readyStatus(model), cached, "the rows stay up under the banner")
        XCTAssertEqual(model.staleError, "git is not installed on the agent host.")

        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))
        await model.load()
        XCTAssertNil(model.staleError, "the next success clears the banner")
    }

    func testAgentOutdatedAndSessionGoneReplaceCachedContent() async throws {
        let (model, _, _) = makeModel()
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("linked_worktree_full"))
        await model.load()

        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Session 's1' not found"}"#)
        await model.load()
        XCTAssertEqual(model.state, .sessionGone)
        XCTAssertTrue(model.isFinished)

        let (outdated, _, _) = makeModel()
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Not Found"}"#)
        await outdated.load()
        XCTAssertEqual(outdated.state, .agentOutdated)
        XCTAssertTrue(outdated.isFinished)
    }

    // MARK: - run()

    func testRunLoadsAtOnceThenPollsWithTheEtagEveryTenSeconds() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("linked_worktree_full"))

        let runner = Task { await model.run() }
        await waitUntil { sleeper.isWaiting }
        XCTAssertEqual(requestURLs, [statusURL], "the first load is immediate, not after a delay")
        XCTAssertEqual(sleeper.durations, [GitChangesModel.pollInterval])
        XCTAssertEqual(GitChangesModel.pollInterval, .seconds(10))

        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))
        sleeper.tick()
        await waitUntil { StubURLProtocol.requests.count == 2 && sleeper.durations.count == 2 }
        XCTAssertEqual(requestURLs[1], "\(statusURL)?etag=\(fullEtag)")
        XCTAssertEqual(readyStatus(model)?.etag, fullEtag, "an unchanged tick keeps the rows")

        runner.cancel()
        await waitUntil { !sleeper.isWaiting }
        XCTAssertFalse(sleeper.isWaiting, "cancelling the screen's task must end the sleep, not leak it")
        XCTAssertEqual(StubURLProtocol.requests.count, 2)
    }

    func testRunEndsAfterAnOutdatedAgentWithoutSleeping() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Not Found"}"#)
        let done = Flag()

        Task {
            await model.run()
            done.raised = true
        }
        await waitUntil { done.raised }

        XCTAssertTrue(done.raised, "the feed must end on its own")
        XCTAssertEqual(model.state, .agentOutdated)
        XCTAssertTrue(sleeper.durations.isEmpty, "there is nothing left to poll")
        XCTAssertEqual(StubURLProtocol.requests.count, 1)
    }

    func testRunEndsAfterSessionGoneEvenMidPoll() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("linked_worktree_full"))
        let done = Flag()

        Task {
            await model.run()
            done.raised = true
        }
        await waitUntil { sleeper.isWaiting }

        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Session 's1' not found"}"#)
        sleeper.tick()
        await waitUntil { done.raised }

        XCTAssertTrue(done.raised)
        XCTAssertEqual(model.state, .sessionGone)
        XCTAssertEqual(sleeper.durations.count, 1, "no further sleep after the terminal answer")
    }

    // MARK: - observeGitChanged()

    func testGitChangedRefreshesOnProjectNameToplevelOrCommonDirOnly() async throws {
        let (model, bus, factory) = makeModel()
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("linked_worktree_full"))
        await model.load()

        Task { await model.observeGitChanged() }
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        // Processed in order: if the matching push below lands as request 2,
        // the unrelated one before it refreshed nothing.
        connection.emit(.text(#"{"type":"git.changed","payload":{"project_name":"other","toplevel":"/elsewhere","common_dir":"/elsewhere/.git"}}"#))
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))
        connection.emit(.text(#"{"type":"git.changed","payload":{"project_name":"proj"}}"#))
        await waitUntil { StubURLProtocol.requests.count == 2 }
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(StubURLProtocol.requests.count, 2, "an unrelated repository must not refresh")

        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))
        connection.emit(.text(#"{"type":"git.changed","payload":{"project_name":"","toplevel":"/home/user/proj/.wt/feature-x"}}"#))
        await waitUntil { StubURLProtocol.requests.count == 3 }

        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))
        connection.emit(.text(#"{"type":"git.changed","payload":{"project_name":"","common_dir":"/home/user/proj/.git"}}"#))
        await waitUntil { StubURLProtocol.requests.count == 4 }

        XCTAssertEqual(
            Array(requestURLs.dropFirst()),
            Array(repeating: "\(statusURL)?etag=\(fullEtag)", count: 3),
            "every push-driven refresh reuses the cached etag"
        )
    }

    func testGitChangedBeforeTheFirstBodyRefreshesWhateverItNames() async throws {
        let (model, bus, factory) = makeModel(projectName: nil)
        Task { await model.observeGitChanged() }
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("detached_head"))
        connection.emit(.text(#"{"type":"git.changed","payload":{"project_name":"unrelated"}}"#))

        await waitUntil { StubURLProtocol.requests.count == 1 }
        XCTAssertEqual(requestURLs, [statusURL])
        XCTAssertEqual(readyStatus(model)?.session_id, "s2")
    }

    // MARK: - diffRoute(for:)

    func testDiffRouteCarriesScopeAndRenameSource() async throws {
        let (model, _, _) = makeModel()
        let entries = try WireJSON.decoder().decode(
            GitStatusResponse.self,
            from: Data(try fixtureBody("linked_worktree_full").utf8)
        ).entries

        XCTAssertEqual(
            model.diffRoute(for: entries[0]),
            .sessionGitDiff(sessionId: "s1", path: "src/conflict.py", scope: "unstaged", origPath: nil)
        )
        XCTAssertEqual(
            model.diffRoute(for: entries[2]),
            .sessionGitDiff(sessionId: "s1", path: "src/new name.py", scope: "staged", origPath: "src/old.py")
        )
        XCTAssertEqual(
            model.diffRoute(for: entries[5]),
            .sessionGitDiff(sessionId: "s1", path: "notes/тест.md", scope: "untracked", origPath: nil)
        )
    }

    // MARK: - Capabilities and mutations

    private let versionURL = "http://agent.example.com/api/version"
    private let ada = GitCommitAuthor(name: "Ada Lovelace", email: "ada@example.com")
    private let previous = GitCommitHead(
        oid: "4890423520f4dcd158b85374f44fc8819a813ac2",
        short_oid: "4890423",
        subject: "Fix the login redirect",
        body: "The callback URL was built before the port was known."
    )

    private func gitURL(_ leaf: String) -> String { "http://agent.example.com/api/sessions/s1/git/\(leaf)" }

    private func versionBody(_ git: String) -> String {
        #"{"name":"mobaide-agent","server_version":"1.4.0","protocol_versions":[7],"min_client_version":1,"capabilities":{"git":\#(git)}}"#
    }

    private func contextBody(_ name: String) throws -> String {
        String(decoding: try FixtureLoader.wireData(path: "dto/agent/GitCommitContextResponse.json", case: name), as: UTF8.self)
    }

    private func committedBody() throws -> String {
        String(decoding: try FixtureLoader.wireData(path: "dto/agent/GitCommitResponse.json", case: "committed"), as: UTF8.self)
    }

    private func sentBody<T: Decodable>(_ type: T.Type, at index: Int) throws -> T {
        try JSONDecoder().decode(type, from: try XCTUnwrap(StubURLProtocol.bodies[safe: index]))
    }

    private func row(_ model: GitChangesModel, _ id: String) throws -> GitChangesRow {
        try XCTUnwrap(model.sections.flatMap(\.rows).first { $0.id == id }, "no row \(id)")
    }

    /// The full fixture on screen with the given `capabilities.git`, the
    /// probe's and the load's traffic already forgotten.
    private func loadedModel(
        git: String = #"{"changes":true,"stage":true,"discard":true,"commit":true}"#
    ) async throws -> GitChangesModel {
        let (model, _, _) = makeModel()
        StubURLProtocol.enqueue(status: 200, body: versionBody(git))
        await model.loadCapabilities()
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("linked_worktree_full"))
        await model.load()
        StubURLProtocol.reset()
        return model
    }

    private let etagStatusURL = "http://agent.example.com/api/sessions/s1/git/status?etag=3f0d4c1e9a7b2c5d6e8f9a0b1c2d3e4f5a6b7c8d"

    func testLoadCapabilitiesReadsTheProbeAndAFailedProbeChangesNothing() async throws {
        let (model, _, _) = makeModel()
        XCTAssertEqual(model.capabilities, .none)
        StubURLProtocol.enqueue(status: 200, body: versionBody(#"{"changes":true,"stage":true,"commit":true}"#))

        await model.loadCapabilities()

        XCTAssertEqual(requestURLs, [versionURL])
        XCTAssertEqual(model.capabilities, GitCapabilities(stage: true, discard: false, commit: true))

        StubURLProtocol.enqueue(status: 503, body: #"{"detail":"Agent is offline"}"#)
        await model.loadCapabilities()
        XCTAssertEqual(model.capabilities, GitCapabilities(stage: true, discard: false, commit: true))
    }

    func testStageSendsThePathThenReloadsAndForgetsTheRow() async throws {
        let model = try await loadedModel()
        let both = try row(model, "unstaged:1:src/both.py")
        model.selection = [both.id, "untracked:1:notes/тест.md"]
        StubURLProtocol.enqueue(status: 200, body: #"{"ok":true}"#)
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))

        await model.stage([both])

        XCTAssertEqual(requestURLs, [gitURL("stage"), etagStatusURL])
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "POST")
        XCTAssertEqual(try sentBody(GitPathsRequest.self, at: 0), GitPathsRequest(paths: ["src/both.py"]))
        XCTAssertEqual(model.selection, ["untracked:1:notes/тест.md"])
        XCTAssertFalse(model.isMutating)
        XCTAssertNil(model.toast)
    }

    func testUnstageNamesBothSidesOfARename() async throws {
        // `git reset` on the new name alone would leave the deletion staged.
        let model = try await loadedModel()
        let rename = try row(model, "staged:1:src/new name.py")
        StubURLProtocol.enqueue(status: 200, body: #"{"ok":true}"#)
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))

        await model.unstage([rename])

        XCTAssertEqual(requestURLs, [gitURL("unstage"), etagStatusURL])
        XCTAssertEqual(try sentBody(GitPathsRequest.self, at: 0), GitPathsRequest(paths: ["src/new name.py", "src/old.py"]))
    }

    func testARefusedMutationRaisesADangerToastKeepsTheSelectionAndStillReloads() async throws {
        // The tree may have moved under the request, so the list is re-read
        // even though nothing was staged.
        let model = try await loadedModel()
        let both = try row(model, "unstaged:1:src/both.py")
        model.selection = [both.id]
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"index_locked","message":"index.lock exists"}}"#)
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))

        await model.stage([both])

        XCTAssertEqual(model.toast?.text, "Another git process is running — try again.")
        XCTAssertEqual(model.toast?.tone, .danger)
        XCTAssertEqual(requestURLs, [gitURL("stage"), etagStatusURL])
        XCTAssertEqual(model.selection, [both.id])
        XCTAssertFalse(model.isMutating)

        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"git_too_old","message":"git 2.26.0 or newer is required"}}"#)
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))
        await model.stage([both])
        XCTAssertEqual(model.toast?.text, "Staging needs git 2.26 or newer on the agent machine.")
    }

    func testTickedRowsThatVanishWithARefreshAreDropped() async throws {
        let model = try await loadedModel()
        model.selection = ["unstaged:1:src/both.py", "untracked:1:notes/тест.md"]
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("upstream_gone"))

        await model.load()

        XCTAssertEqual(model.selection, [])
        XCTAssertEqual(model.sections.map(\.group), ["unstaged"])
    }

    // MARK: - Discard

    func testDiscardGoesThroughThePromptThenOneRequestPerScope() async throws {
        let model = try await loadedModel()
        let both = try row(model, "unstaged:1:src/both.py")
        let notes = try row(model, "untracked:1:notes/тест.md")

        model.requestDiscard([])
        XCTAssertNil(model.discardPrompt)

        model.requestDiscard([both, notes])
        XCTAssertEqual(model.discardPrompt?.title, "Discard 2 files?")
        model.dismissDiscard()
        XCTAssertNil(model.discardPrompt)
        await model.confirmDiscard()
        XCTAssertEqual(requestURLs, [], "a dismissed prompt sends nothing")

        model.requestDiscard([both, notes])
        StubURLProtocol.enqueue(status: 200, body: #"{"ok":true}"#)
        StubURLProtocol.enqueue(status: 200, body: #"{"ok":true}"#)
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))
        await model.confirmDiscard()

        XCTAssertNil(model.discardPrompt)
        XCTAssertEqual(requestURLs, [gitURL("discard"), gitURL("discard"), etagStatusURL])
        XCTAssertEqual(try sentBody(GitDiscardRequest.self, at: 0), GitDiscardRequest(paths: ["src/both.py"], scope: "unstaged"))
        XCTAssertEqual(try sentBody(GitDiscardRequest.self, at: 1), GitDiscardRequest(paths: ["notes/тест.md"], scope: "untracked"))
        XCTAssertFalse(model.isMutating)
    }

    func testTheConfirmStillHasItsRowsAfterTheDialogBindingClearedThePrompt() async throws {
        // SwiftUI writes the dialog's `isPresented` false before the confirm
        // action runs; the rows live beside the prompt, not in it.
        let model = try await loadedModel()
        let notes = try row(model, "untracked:1:notes/тест.md")
        model.requestDiscard([notes])
        XCTAssertEqual(model.discardPrompt?.title, "Delete untracked file?")
        model.discardPrompt = nil
        StubURLProtocol.enqueue(status: 200, body: #"{"ok":true}"#)
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))

        await model.confirmDiscard()

        XCTAssertEqual(requestURLs, [gitURL("discard"), etagStatusURL])
        XCTAssertEqual(try sentBody(GitDiscardRequest.self, at: 0), GitDiscardRequest(paths: ["notes/тест.md"], scope: "untracked"))
    }

    // MARK: - Commit

    func testOpenCommitLoadsTheContextIntoTheSheetAndCloseDropsIt() async throws {
        let model = try await loadedModel()
        StubURLProtocol.enqueue(status: 200, body: try contextBody("ready"))

        model.openCommit()

        XCTAssertEqual(model.commitSheet, CommitSheetState(), "the sheet is up at once, loading")
        await waitUntil { model.commitSheet?.context != .loading }
        XCTAssertEqual(requestURLs, [gitURL("commit/context")])
        XCTAssertEqual(model.commitSheet?.context, .ready(author: ada, head: previous))

        model.closeCommit()
        XCTAssertNil(model.commitSheet)
    }

    func testCommitComposesTheMessageClosesTheSheetToastsAndReloads() async throws {
        let model = try await loadedModel()
        model.commitSheet = CommitSheetState(context: .ready(author: ada, head: nil))
        StubURLProtocol.enqueue(status: 200, body: try committedBody())
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("unchanged"))

        await model.commit(summary: "  Fix the login redirect ", description: "Why.\n", amend: false, noVerify: true)

        XCTAssertEqual(requestURLs, [gitURL("commit"), etagStatusURL])
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "POST")
        XCTAssertEqual(
            try sentBody(GitCommitRequest.self, at: 0),
            GitCommitRequest(message: "Fix the login redirect\n\nWhy.", amend: false, no_verify: true)
        )
        XCTAssertNil(model.commitSheet)
        XCTAssertEqual(model.toast?.text, "Committed 4890423 · Fix the login redirect")
        XCTAssertEqual(model.toast?.tone, .ok)
        XCTAssertFalse(model.isMutating)
    }

    func testAHookRefusalStaysInTheSheetWithTheHookOutput() async throws {
        let model = try await loadedModel()
        model.commitSheet = CommitSheetState(context: .ready(author: ada, head: nil))
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"hook_failed","message":"lint: nope"}}"#)

        await model.commit(summary: "hooked", description: "", amend: false, noVerify: false)

        XCTAssertEqual(requestURLs, [gitURL("commit")], "no reload: nothing changed")
        XCTAssertEqual(model.commitSheet?.isCommitting, false)
        XCTAssertEqual(model.commitSheet?.error, CommitError(message: "A commit hook rejected the commit.", output: "lint: nope"))
        XCTAssertNil(model.toast)
        XCTAssertFalse(model.isMutating)

        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"identity_required","message":"no identity"}}"#)
        await model.commit(summary: "who", description: "", amend: false, noVerify: false)
        XCTAssertEqual(
            model.commitSheet?.error,
            CommitError(message: "Git doesn't know who you are on the agent machine.", output: nil),
            "only a hook's output is worth showing"
        )

        model.closeCommit()
        XCTAssertNil(model.commitSheet)
    }
}

private extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}
