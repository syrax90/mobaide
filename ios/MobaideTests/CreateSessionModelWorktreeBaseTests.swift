import XCTest
@testable import Mobaide

/// The New Session base-branch picker: capability gate, preselection, project
/// change reset, and `base_branch` on the request per mode.
@MainActor
final class CreateSessionModelWorktreeBaseTests: XCTestCase {
    private static let projectsJSON = #"""
    [{"name":"repo","path":"/r","is_git":true},
     {"name":"other","path":"/o","is_git":true},
     {"name":"plain","path":"/p","is_git":false}]
    """#

    private static func versionJSON(worktreeBase: Bool) -> String {
        #"{"name":"agent","server_version":"1.0.0","protocol_versions":[7],"min_client_version":1,"#
            + #""capabilities":{"git":{"branches":true\#(worktreeBase ? #","worktree_base":true"# : "")}}}"#
    }

    private static func basesJSON(default def: String?, lastUsed: String?) -> String {
        func json(_ value: String?) -> String { value.map { "\"\($0)\"" } ?? "null" }
        return #"{"default":\#(json(def)),"last_used":\#(json(lastUsed)),"refs":["#
            + #"{"ref":"origin/main","kind":"remote","subject":"m","committed_at":"2026-11-08T10:15:00+00:00"},"#
            + #"{"ref":"origin/develop","kind":"remote","subject":null,"committed_at":null},"#
            + #"{"ref":"wip","kind":"local"}]}"#
    }

    private func stubbedModel() -> CreateSessionModel {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(baseURL: { "http://agent.example.com" }, tokens: FakeAgentTokens(token: AgentToken(value: "tok")), client: client)
        let repo = SessionRepository(agentAPI: agentAPI, agentEventBus: bus, terminalSessionStore: TerminalTestSupport.store(harness))
        let wsClient = AutoScanWsClient(
            webSocketFactory: .live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            urlProvider: { nil },
            isAuthenticated: { false }
        )
        return CreateSessionModel(
            agentAPI: agentAPI,
            sessionRepository: repo,
            preferencesStore: PreferencesStore(defaults: UserDefaults(suiteName: "wb-\(UUID().uuidString)")!),
            tokenStore: harness.tokenStore,
            customAgentTypesRepository: CustomAgentTypesRepository(
                centralAPI: TerminalTestSupport.stubbedCentralAPI(),
                userIdentityStore: SessionHarness(userLoggedIn: true).userIdentityStore
            ),
            autoScanRepository: AutoScanRepository(agentAPI: agentAPI, agentEventBus: bus, wsClient: wsClient)
        )
    }

    /// Projects loaded, the version probed, `repo` selected with the worktree on.
    private func readyModel(worktreeBase: Bool = true, mode: CreateSessionModel.Mode = .standard) async -> CreateSessionModel {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: Self.projectsJSON)
        StubURLProtocol.enqueue(status: 200, body: Self.versionJSON(worktreeBase: worktreeBase))
        let model = stubbedModel()
        await model.load()
        await model.refreshCliAvailability()
        model.selectStoredProject("repo")
        model.mode = mode
        model.worktree = true
        return model
    }

    private func loaded(_ model: CreateSessionModel, default def: String?, lastUsed: String?) async {
        StubURLProtocol.enqueue(status: 200, body: Self.basesJSON(default: def, lastUsed: lastUsed))
        await model.loadWorktreeBases()
    }

    // MARK: - Capability gate

    func testCapabilityKeyIsParsed() throws {
        let wire = Data(Self.versionJSON(worktreeBase: true).utf8)
        let probe = try WireJSON.decoder().decode(VersionInfoResponse.self, from: wire)
        XCTAssertTrue(probe.gitCapabilities.worktreeBase)
        let old = try WireJSON.decoder().decode(VersionInfoResponse.self, from: Data(Self.versionJSON(worktreeBase: false).utf8))
        XCTAssertFalse(old.gitCapabilities.worktreeBase)
    }

    func testPickerHiddenWithoutCapabilityAndNoRequestIsMade() async throws {
        let model = await readyModel(worktreeBase: false)
        XCTAssertFalse(model.showsBaseBranchPicker)
        XCTAssertNil(model.worktreeBasesLoadKey)
        let before = StubURLProtocol.requests.count
        await model.loadWorktreeBases()
        XCTAssertEqual(StubURLProtocol.requests.count, before, "an older agent is never asked for the list")
        XCTAssertEqual(model.worktreeBases, .idle)
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertNil(request.claude_options?.base_branch)
    }

    func testPickerVisibilityFollowsWorktreeProjectAndMode() async {
        let model = await readyModel()
        XCTAssertTrue(model.showsBaseBranchPicker)
        XCTAssertEqual(model.worktreeBasesLoadKey, "repo")
        model.worktree = false
        XCTAssertFalse(model.showsBaseBranchPicker)
        model.worktree = true
        model.mode = .autoScan
        XCTAssertFalse(model.showsBaseBranchPicker, "Auto Scan never shows the picker")
        model.mode = .shell
        model.selectStoredProject("plain")
        XCTAssertFalse(model.showsBaseBranchPicker, "a non-git project has no worktree")
    }

    // MARK: - Loading and preselection

    func testLoadHitsTheProjectRoute() async throws {
        let model = await readyModel()
        await loaded(model, default: "origin/main", lastUsed: nil)
        let url = try XCTUnwrap(StubURLProtocol.requests.last?.url?.absoluteString)
        XCTAssertEqual(url, "http://agent.example.com/api/projects/repo/git/worktree-bases")
    }

    func testPreselectsLastUsedOverDefault() async {
        let model = await readyModel()
        await loaded(model, default: "origin/main", lastUsed: "origin/develop")
        XCTAssertEqual(model.baseBranch, "origin/develop")
    }

    func testPreselectsDefaultWithoutLastUsed() async {
        let model = await readyModel()
        await loaded(model, default: "origin/main", lastUsed: nil)
        XCTAssertEqual(model.baseBranch, "origin/main")
    }

    func testPreselectsNilWithNeither() async {
        let model = await readyModel()
        await loaded(model, default: nil, lastUsed: nil)
        XCTAssertNil(model.baseBranch)
        XCTAssertEqual(WorktreeBaseDisplay.label(model.baseBranch), "Default branch")
    }

    func testLoadFailureShowsErrorAndSendsNoBase() async throws {
        let model = await readyModel()
        StubURLProtocol.enqueue(status: 500, body: #"{"detail":"boom"}"#)
        await model.loadWorktreeBases()
        XCTAssertEqual(model.worktreeBases, .failed)
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(request.claude_options?.worktree, true, "creating still works")
        XCTAssertNil(request.claude_options?.base_branch)

        StubURLProtocol.enqueue(status: 200, body: Self.basesJSON(default: "origin/main", lastUsed: nil))
        await model.loadWorktreeBases(force: true)
        XCTAssertEqual(model.baseBranch, "origin/main", "Retry loads and preselects")
    }

    func testListIsLoadedOncePerProject() async {
        let model = await readyModel()
        await loaded(model, default: "origin/main", lastUsed: nil)
        let count = StubURLProtocol.requests.count
        await model.loadWorktreeBases()
        XCTAssertEqual(StubURLProtocol.requests.count, count)
    }

    func testManualPickSurvivesReloadOfSameProject() async {
        let model = await readyModel()
        await loaded(model, default: "origin/main", lastUsed: nil)
        model.pickBaseBranch("wip")
        StubURLProtocol.enqueue(status: 200, body: Self.basesJSON(default: "origin/main", lastUsed: "origin/develop"))
        await model.loadWorktreeBases(force: true)
        XCTAssertEqual(model.baseBranch, "wip")
    }

    func testProjectChangeResetsPickAndList() async {
        let model = await readyModel()
        await loaded(model, default: "origin/main", lastUsed: nil)
        model.pickBaseBranch("wip")
        model.selectStoredProject("other")
        XCTAssertNil(model.baseBranch)
        XCTAssertEqual(model.worktreeBases, .idle)
        XCTAssertEqual(model.worktreeBasesLoadKey, "other")

        StubURLProtocol.enqueue(status: 200, body: Self.basesJSON(default: "origin/main", lastUsed: "origin/develop"))
        await model.loadWorktreeBases()
        XCTAssertEqual(model.baseBranch, "origin/develop", "the new project's preselection applies")
        XCTAssertEqual(StubURLProtocol.requests.last?.url?.path, "/api/projects/other/git/worktree-bases")
    }

    func testReselectingTheSameProjectKeepsThePick() async {
        let model = await readyModel()
        await loaded(model, default: "origin/main", lastUsed: nil)
        model.pickBaseBranch("wip")
        model.selectStoredProject("repo")
        XCTAssertEqual(model.baseBranch, "wip")
    }

    // MARK: - Request encoding

    func testClaudeCarriesBaseInOptions() async throws {
        let model = await readyModel()
        await loaded(model, default: "origin/main", lastUsed: nil)
        model.pickBaseBranch("wip")
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(request.claude_options?.base_branch, "wip")
        XCTAssertNil(request.base_branch)
    }

    func testCodexAndAntigravityCarryBaseInOptions() async throws {
        let model = await readyModel(mode: .codex)
        await loaded(model, default: "origin/main", lastUsed: nil)
        let codex = try XCTUnwrap(model.buildRequest(mode: .codex))
        XCTAssertEqual(codex.codex_options?.base_branch, "origin/main")
        XCTAssertNil(codex.base_branch)

        model.mode = .antigravity
        let antigravity = try XCTUnwrap(model.buildRequest(mode: .antigravity))
        XCTAssertEqual(antigravity.antigravity_options?.base_branch, "origin/main")
        XCTAssertNil(antigravity.base_branch)
    }

    func testTerminalAndCustomCarryBaseTopLevel() async throws {
        let model = await readyModel(mode: .shell)
        await loaded(model, default: "origin/main", lastUsed: nil)
        let shell = try XCTUnwrap(model.buildRequest(mode: .shell))
        XCTAssertEqual(shell.base_branch, "origin/main")
        XCTAssertEqual(shell.worktree, true)

        model.mode = .custom("abc123def456")
        let custom = try XCTUnwrap(model.buildRequest(mode: .custom("abc123def456")))
        XCTAssertEqual(custom.base_branch, "origin/main")
    }

    func testWorktreeOffSendsNoBase() async throws {
        let model = await readyModel()
        await loaded(model, default: "origin/main", lastUsed: nil)
        model.worktree = false
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertNil(request.claude_options?.base_branch)
    }

    func testAutoScanOptionsNeverCarryBase() async {
        let model = await readyModel(mode: .autoScan)
        XCTAssertNil(model.buildRequest(mode: .autoScan))
        XCTAssertNil(model.autoScanClaudeOptions().base_branch)
    }

    func testBaseBranchIsOnTheWireAndNilIsAbsent() throws {
        let withBase = CreateSessionRequest(project_name: "repo", worktree: true, base_branch: "origin/main")
        let json = try XCTUnwrap(String(data: WireJSON.encoder().encode(withBase), encoding: .utf8))
        XCTAssertTrue(json.contains(#""base_branch":"origin\/main""#) || json.contains(#""base_branch":"origin/main""#))

        let codex = CodexOptionsModel(worktree: true)
        let codexJSON = try XCTUnwrap(String(data: WireJSON.encoder().encode(codex), encoding: .utf8))
        XCTAssertFalse(codexJSON.contains("base_branch"), "nil keeps the agent default by staying off the wire")
    }

    // MARK: - Display

    func testFilterIsCaseInsensitiveSubstring() {
        let refs = [
            WorktreeBaseRef(ref: "origin/Main", kind: "remote"),
            WorktreeBaseRef(ref: "wip", kind: "local"),
        ]
        XCTAssertEqual(WorktreeBaseDisplay.filter(refs, query: "main").map(\.ref), ["origin/Main"])
        XCTAssertEqual(WorktreeBaseDisplay.filter(refs, query: "  ").count, 2)
        XCTAssertTrue(WorktreeBaseDisplay.filter(refs, query: "zzz").isEmpty)
    }

    func testRelativeTimeNeedsAReadableDate() {
        XCTAssertNil(WorktreeBaseDisplay.relativeTime(nil))
        XCTAssertNil(WorktreeBaseDisplay.relativeTime("garbage"))
        XCTAssertEqual(WorktreeBaseDisplay.relativeTime("2020-01-01T00:00:00+00:00")?.hasSuffix("d ago"), true)
    }
}
