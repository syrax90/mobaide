import XCTest
@testable import Mobaide

/// The per-file Diff model over the real `AgentAPI` (stubbed transport):
/// what it asks the agent for and which state each answer lands in. Row
/// derivation itself is covered by `GitDiffDisplayTests`.
@MainActor
final class GitDiffModelTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeModel(
        path: String = "src/a.py",
        scope: String = GitDiffResponse.scopeUnstaged,
        origPath: String? = nil
    ) -> GitDiffModel {
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: client
        )
        return GitDiffModel(sessionId: "s1", path: path, scope: scope, origPath: origPath, agentAPI: agentAPI)
    }

    private func fixtureBody(_ name: String) throws -> String {
        String(decoding: try FixtureLoader.wireData(path: "dto/agent/GitDiffResponse.json", case: name), as: UTF8.self)
    }

    private var requestURLs: [String] {
        StubURLProtocol.requests.compactMap { $0.url?.absoluteString }
    }

    private let diffURL = "http://agent.example.com/api/sessions/s1/git/diff"

    func testLoadAsksForPathScopeDefaultContextAndRenameSource() async throws {
        let model = makeModel(path: "src/new name.py", scope: GitDiffResponse.scopeStaged, origPath: "src/old.py")
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("renamed_with_heading"))

        await model.load()

        XCTAssertEqual(requestURLs, ["\(diffURL)?path=src%2Fnew%20name.py&scope=staged&context=3&orig_path=src%2Fold.py"])
        guard case .ready(let diff, let rows, let metrics) = model.state else {
            return XCTFail("expected ready, got \(model.state)")
        }
        XCTAssertEqual(diff.change, GitDiffResponse.changeRenamed)
        XCTAssertEqual(rows.map(\.id), ["h0", "h0:l0", "h0:l1", "h0:l2"])
        XCTAssertEqual(metrics.gutterWidth, metrics.cell * 2, "two-digit gutters")
        XCTAssertFalse(model.isLoading)
        XCTAssertEqual(model.fileName, "new name.py")
    }

    func testFullFileIsSentAsTheAgentCeilingAndBumpsTheLoadKey() async throws {
        let model = makeModel()
        XCTAssertEqual(model.loadKey, GitDiffModel.LoadKey(context: 3, reloads: 0))
        XCTAssertEqual(GitDiffModel.contextChoices.map(\.0), [3, 5000])
        XCTAssertEqual(GitDiffModel.contextChoices.map(\.1), ["Changes", "Full file"])

        model.context = GitDiffModel.contextFull
        XCTAssertEqual(model.loadKey, GitDiffModel.LoadKey(context: 5000, reloads: 0))
        StubURLProtocol.enqueue(status: 200, body: try fixtureBody("text_modified"))
        await model.load()

        XCTAssertEqual(requestURLs, ["\(diffURL)?path=src%2Fa.py&scope=unstaged&context=5000"])
    }

    func testRetryBumpsTheLoadKeyWithoutTouchingTheContext() {
        let model = makeModel()
        model.context = 10

        model.retry()
        model.retry()

        XCTAssertEqual(model.loadKey, GitDiffModel.LoadKey(context: 10, reloads: 2))
    }

    func testEachFailureShapeLandsInItsOwnState() async throws {
        let outdated = makeModel()
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Not Found"}"#)
        await outdated.load()
        XCTAssertEqual(outdated.state, .agentOutdated)

        let gone = makeModel()
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Session 's1' not found"}"#)
        await gone.load()
        XCTAssertEqual(gone.state, .sessionGone)

        let refused = makeModel()
        StubURLProtocol.enqueue(status: 504, body: #"{"detail":{"code":"git_timeout","message":null}}"#)
        await refused.load()
        XCTAssertEqual(refused.state, .error("git took too long to answer. Try again."))
    }

    // MARK: - Stage / Unstage from the review

    private func versionBody(_ git: String) -> String {
        #"{"name":"mobaide-agent","server_version":"1.4.0","protocol_versions":[7],"min_client_version":1,"capabilities":{"git":\#(git)}}"#
    }

    /// A model whose agent advertises `stage`, with the probe's traffic already forgotten.
    private func stagingModel(
        path: String = "src/a.py",
        scope: String = GitDiffResponse.scopeUnstaged,
        origPath: String? = nil
    ) async -> GitDiffModel {
        let model = makeModel(path: path, scope: scope, origPath: origPath)
        StubURLProtocol.enqueue(status: 200, body: versionBody(#"{"changes":true,"stage":true}"#))
        await model.loadCapabilities()
        StubURLProtocol.reset()
        return model
    }

    func testStageActionFollowsTheRouteScopeOnceTheAgentAdvertisesStage() async {
        let unstaged = makeModel()
        XCTAssertNil(unstaged.stageAction, "nothing before the probe")
        StubURLProtocol.enqueue(status: 200, body: versionBody(#"{"changes":true,"stage":true}"#))
        await unstaged.loadCapabilities()
        XCTAssertEqual(requestURLs, ["http://agent.example.com/api/version"])
        XCTAssertEqual(unstaged.stageAction?.label, "Stage")
        XCTAssertEqual(unstaged.stageAction?.stage, true)

        let untracked = await stagingModel(scope: GitDiffResponse.scopeUntracked)
        XCTAssertEqual(untracked.stageAction?.label, "Stage")

        let staged = await stagingModel(path: "src/new.py", scope: GitDiffResponse.scopeStaged, origPath: "src/old.py")
        XCTAssertEqual(staged.stageAction?.label, "Unstage")
        XCTAssertEqual(staged.stageAction?.stage, false)

        let odd = await stagingModel(scope: "weird")
        XCTAssertNil(odd.stageAction)
    }

    func testStageActionStaysAbsentOnAReadOnlyAgentOrAFailedProbe() async {
        let readOnly = makeModel()
        StubURLProtocol.enqueue(status: 200, body: versionBody(#"{"changes":true}"#))
        await readOnly.loadCapabilities()
        XCTAssertNil(readOnly.stageAction)

        let offline = makeModel()
        StubURLProtocol.enqueue(status: 503, body: #"{"detail":"Agent is offline"}"#)
        await offline.loadCapabilities()
        XCTAssertNil(offline.stageAction)
        XCTAssertEqual(offline.capabilities, .none)
    }

    func testRunStageActionSendsBothSidesOfARenameAndMarksTheMove() async throws {
        let model = await stagingModel(path: "src/new.py", scope: GitDiffResponse.scopeStaged, origPath: "src/old.py")
        StubURLProtocol.enqueue(status: 200, body: #"{"ok":true}"#)

        await model.runStageAction()

        XCTAssertEqual(requestURLs, ["http://agent.example.com/api/sessions/s1/git/unstage"])
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "POST")
        let sent = try JSONDecoder().decode(GitPathsRequest.self, from: try XCTUnwrap(StubURLProtocol.bodies.first))
        XCTAssertEqual(sent, GitPathsRequest(paths: ["src/new.py", "src/old.py"]))
        XCTAssertTrue(model.didMove)
        XCTAssertFalse(model.isMutating)
        XCTAssertNil(model.toast)
    }

    func testRunStageActionReportsARefusalAndStaysOnTheFile() async {
        let model = await stagingModel()
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"pathspec_mismatch","message":"did not match"}}"#)

        await model.runStageAction()

        XCTAssertEqual(model.toast?.text, "That file changed under you — refreshed.")
        XCTAssertEqual(model.toast?.tone, .danger)
        XCTAssertFalse(model.didMove)
        XCTAssertFalse(model.isMutating)
    }
}
