import XCTest
@testable import Mobaide

@MainActor
final class CreateSessionModelTests: XCTestCase {
    private struct StubAgentTokens: AgentTokenProviding {
        func validToken() async -> AgentToken? { nil }
        func refreshedToken() async -> String? { nil }
    }

    private func isolatedPrefs() -> PreferencesStore {
        PreferencesStore(defaults: UserDefaults(suiteName: "cw-\(UUID().uuidString)")!)
    }

    private func makeModel(prefs: PreferencesStore? = nil) -> CreateSessionModel {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(
                transportFactory: FakeWebSocketTransportFactory(),
                sleeper: RecordingSleeper()
            ),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
        let repo = SessionRepository(agentAPI: agentAPI, agentEventBus: bus, terminalSessionStore: TerminalTestSupport.store(harness))
        return CreateSessionModel(agentAPI: agentAPI, sessionRepository: repo, preferencesStore: prefs ?? isolatedPrefs())
    }

    private func modelWithProjects(_ projectsJSON: String) async -> CreateSessionModel {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: projectsJSON)
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(baseURL: { "http://agent.example.com" }, tokens: FakeAgentTokens(token: AgentToken(value: "tok")), client: client)
        let repo = SessionRepository(agentAPI: agentAPI, agentEventBus: bus, terminalSessionStore: TerminalTestSupport.store(harness))
        let model = CreateSessionModel(agentAPI: agentAPI, sessionRepository: repo, preferencesStore: isolatedPrefs())
        await model.load()
        return model
    }

    // MARK: - Request building

    func testClaudeStoredProjectDefaults() {
        let model = makeModel()
        model.selection = .stored("alpha")
        let request = model.buildRequest(mode: .standard)
        XCTAssertEqual(request.project_name, "alpha")
        XCTAssertNil(request.cwd)
        XCTAssertNil(request.cli)
        XCTAssertNil(request.codex_options)
        XCTAssertNil(request.antigravity_options)
        XCTAssertEqual(request.claude_options?.effort, "high", "wizard state default differs from DTO nil default")
        XCTAssertEqual(request.claude_options?.skip_permissions, true)
        XCTAssertEqual(request.claude_options?.worktree, false)
    }

    func testClaudeFastModeLocksOpus() {
        let model = makeModel()
        model.selection = .stored("a")
        model.claudeFastMode = true
        let request = model.buildRequest(mode: .standard)
        XCTAssertEqual(request.claude_options?.model, "opus")
        XCTAssertEqual(request.claude_options?.fast_mode, true)
    }

    func testClaudeUltracodeLocksXhighEffort() {
        let model = makeModel()
        model.selection = .stored("a")
        model.claudeUltracode = true
        XCTAssertEqual(model.buildRequest(mode: .standard).claude_options?.effort, "xhigh")
    }

    func testClaudeEffortClampedWhenModelNarrows() {
        let model = makeModel()
        model.selection = .stored("a")
        model.claudeModel = "opus"
        model.claudeEffort = "max"
        XCTAssertEqual(model.buildRequest(mode: .standard).claude_options?.effort, "max")
        model.claudeModel = "haiku" // no longer supports "max"
        XCTAssertEqual(
            model.buildRequest(mode: .standard).claude_options?.effort, "high",
            "a stale effort unsupported by the new model clamps to a valid value"
        )
    }

    func testCodexUsesCliAndStateDefaults() {
        let model = makeModel()
        model.selection = .stored("a")
        let request = model.buildRequest(mode: .codex)
        XCTAssertEqual(request.cli, "codex")
        XCTAssertEqual(request.codex_options?.approval, "on-request")
        XCTAssertEqual(request.codex_options?.reasoning_effort, "high")
        XCTAssertEqual(request.codex_options?.sandbox, "workspace-write")
        XCTAssertNil(request.claude_options)
    }

    func testAntigravityExclusivityAndAddDirs() {
        let model = makeModel()
        model.selection = .stored("a")
        model.setAntigravitySkipPermissions(true)
        model.setAntigravitySandbox(true) // must force skip-permissions off
        XCTAssertFalse(model.antigravitySkipPermissions)
        XCTAssertTrue(model.antigravitySandbox)
        model.antigravityAddDirs = "one\ntwo, three"
        let request = model.buildRequest(mode: .antigravity)
        XCTAssertEqual(request.cli, "antigravity")
        XCTAssertEqual(request.antigravity_options?.add_dirs, ["one", "two", "three"])
        XCTAssertEqual(request.antigravity_options?.sandbox, true)
        XCTAssertEqual(request.antigravity_options?.skip_permissions, false)
    }

    func testShellCarriesWorktreeOnTopLevel() async {
        // Shell worktree/branch ride the top-level request, but only for a
        // stored git project (worktree is git-gated).
        let model = await modelWithProjects(#"[{"name":"a","path":"/p","is_git":true}]"#)
        model.selectStoredProject("a")
        model.worktree = true
        model.branchName = "feat"
        let request = model.buildRequest(mode: .shell)
        XCTAssertNil(request.cli)
        XCTAssertNil(request.claude_options)
        XCTAssertEqual(request.project_name, "a")
        XCTAssertEqual(request.worktree, true)
        XCTAssertEqual(request.branch_name, "feat")
    }

    func testCustomPathForcesWorktreeOff() {
        let model = makeModel()
        model.setCustomPath("/abs/path")
        model.worktree = true
        let request = model.buildRequest(mode: .standard)
        XCTAssertEqual(request.cwd, "/abs/path")
        XCTAssertNil(request.project_name)
        XCTAssertEqual(request.worktree, false)
        XCTAssertEqual(request.claude_options?.worktree, false)
    }

    func testCustomPathTrimmedInRequest() {
        let model = makeModel()
        model.setCustomPath("  /abs/path  ")
        XCTAssertEqual(model.buildRequest(mode: .standard).cwd, "/abs/path")
    }

    func testNonGitStoredProjectDropsWorktree() async {
        let model = await modelWithProjects(#"[{"name":"plain","path":"/p","is_git":false}]"#)
        model.selectStoredProject("plain")
        model.worktree = true
        model.branchName = "x"
        let request = model.buildRequest(mode: .standard)
        XCTAssertFalse(model.showsGitOptions)
        XCTAssertEqual(request.claude_options?.worktree, false, "a non-git project must not send a stale worktree toggle")
        XCTAssertNil(request.claude_options?.branch_name)
    }

    func testGitStoredProjectKeepsWorktree() async {
        let model = await modelWithProjects(#"[{"name":"repo","path":"/p","is_git":true}]"#)
        model.selectStoredProject("repo")
        model.worktree = true
        model.branchName = "feat"
        let request = model.buildRequest(mode: .standard)
        XCTAssertTrue(model.showsGitOptions)
        XCTAssertEqual(request.claude_options?.worktree, true)
        XCTAssertEqual(request.claude_options?.branch_name, "feat")
    }

    // MARK: - Validation / helpers

    func testValidateCustomPath() {
        XCTAssertNil(CreateSessionModel.validateCustomPath("/abs"))
        XCTAssertNil(CreateSessionModel.validateCustomPath(#"C:\Users"#))
        XCTAssertEqual(CreateSessionModel.validateCustomPath("relative"), "Path must be absolute")
        XCTAssertEqual(CreateSessionModel.validateCustomPath("   "), "Path is required")
    }

    func testParseAddDirs() {
        XCTAssertEqual(CreateSessionModel.parseAddDirs("a\nb, c ,,\n d"), ["a", "b", "c", "d"])
    }

    func testParseAddDirsTrimsCarriageReturns() {
        XCTAssertEqual(CreateSessionModel.parseAddDirs("/a\r\n/b,\r/c"), ["/a", "/b", "/c"])
    }

    func testAntigravityModelFallbackPresent() {
        let model = makeModel()
        XCTAssertFalse(model.antigravityModels.isEmpty, "a fallback list must exist before the agy probe")
        XCTAssertEqual(model.antigravityModels, CreateSessionModel.antigravityModelsFallback)
    }

    func testProjectContinueGating() {
        let model = makeModel()
        XCTAssertFalse(model.canContinueProject)
        model.selectStoredProject("a")
        XCTAssertTrue(model.canContinueProject)
        model.setCustomPath("relative")
        XCTAssertFalse(model.canContinueProject)
        model.setCustomPath("/abs")
        XCTAssertTrue(model.canContinueProject)
    }

    func testDefaultAvailabilityIsOptimistic() {
        let model = makeModel()
        XCTAssertTrue(model.claudeEnabled)
        XCTAssertTrue(model.codexEnabled)
        XCTAssertTrue(model.antigravityEnabled)
        XCTAssertTrue(model.isEnabled(.shell))
    }

    func testPrefillFromPreferences() {
        let prefs = isolatedPrefs()
        prefs.setLastUsedClaudeOptions(ClaudeLaunchPrefs(
            model: "sonnet", effort: "low", fastMode: false, ultracode: false, appendSystemPrompt: "sys"
        ))
        prefs.setLastUsedCodexOptions(CodexLaunchPrefs(
            model: "gpt-5.4", sandbox: "read-only", approval: "never", fullAuto: true, reasoningEffort: "medium"
        ))
        let model = makeModel(prefs: prefs)
        XCTAssertEqual(model.claudeModel, "sonnet")
        XCTAssertEqual(model.claudeEffort, "low")
        XCTAssertEqual(model.claudeAppendSystemPrompt, "sys")
        XCTAssertEqual(model.codexModel, "gpt-5.4")
        XCTAssertEqual(model.codexApproval, "never")
        XCTAssertTrue(model.codexFullAuto)
    }
}
