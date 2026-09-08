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
        projectName: String? = "proj"
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
            sleeper: sleeper
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
}
