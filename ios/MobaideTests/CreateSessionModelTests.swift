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

    private func idleWsClient() -> AutoScanWsClient {
        AutoScanWsClient(
            webSocketFactory: .live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            urlProvider: { nil },
            isAuthenticated: { false }
        )
    }

    /// Repository over `StubURLProtocol` with its own logged-in identity —
    /// pass the same instance to the model when a test seeds or inspects the
    /// custom-type cache.
    private func makeTypesRepo() -> CustomAgentTypesRepository {
        CustomAgentTypesRepository(
            centralAPI: TerminalTestSupport.stubbedCentralAPI(),
            userIdentityStore: SessionHarness(userLoggedIn: true).userIdentityStore
        )
    }

    /// `SessionHarness(agentConnected: true)` saves an agent URL but no agent
    /// id, so the wizard scopes recents by URL.
    private let harnessAgentKey = "https://agent.example.com"

    /// A device that never paired and never reached an agent directly: both
    /// halves of the wizard's agent scope key are nil.
    private func unscopedTokenStore() -> TokenStore {
        TokenStore(keychain: InMemoryKeychainStore())
    }

    private func makeModel(
        prefs: PreferencesStore? = nil,
        typesRepo: CustomAgentTypesRepository? = nil,
        tokenStore: TokenStore? = nil
    ) -> CreateSessionModel {
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
        return CreateSessionModel(
            agentAPI: agentAPI,
            sessionRepository: repo,
            preferencesStore: prefs ?? isolatedPrefs(),
            tokenStore: tokenStore ?? harness.tokenStore,
            customAgentTypesRepository: typesRepo ?? makeTypesRepo(),
            autoScanRepository: AutoScanRepository(agentAPI: agentAPI, agentEventBus: bus, wsClient: idleWsClient())
        )
    }

    private func stubbedModel(
        prefs: PreferencesStore? = nil,
        typesRepo: CustomAgentTypesRepository? = nil,
        tokenStore: TokenStore? = nil
    ) -> CreateSessionModel {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(baseURL: { "http://agent.example.com" }, tokens: FakeAgentTokens(token: AgentToken(value: "tok")), client: client)
        let repo = SessionRepository(agentAPI: agentAPI, agentEventBus: bus, terminalSessionStore: TerminalTestSupport.store(harness))
        return CreateSessionModel(
            agentAPI: agentAPI,
            sessionRepository: repo,
            preferencesStore: prefs ?? isolatedPrefs(),
            tokenStore: tokenStore ?? harness.tokenStore,
            customAgentTypesRepository: typesRepo ?? makeTypesRepo(),
            autoScanRepository: AutoScanRepository(agentAPI: agentAPI, agentEventBus: bus, wsClient: idleWsClient())
        )
    }

    private func modelWithProjects(_ projectsJSON: String) async -> CreateSessionModel {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: projectsJSON)
        let model = stubbedModel()
        await model.load()
        return model
    }

    // MARK: - Request building

    func testClaudeStoredProjectDefaults() throws {
        let model = makeModel()
        model.selection = .stored("alpha")
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(request.project_name, "alpha")
        XCTAssertNil(request.cwd)
        XCTAssertNil(request.cli)
        XCTAssertNil(request.codex_options)
        XCTAssertNil(request.antigravity_options)
        XCTAssertEqual(request.claude_options?.effort, "high", "wizard state default differs from DTO nil default")
        XCTAssertEqual(request.claude_options?.skip_permissions, false)
        XCTAssertEqual(request.claude_options?.worktree, false)
    }

    func testClaudeFastModeLocksOpus() throws {
        let model = makeModel()
        model.selection = .stored("a")
        model.claudeFastMode = true
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(request.claude_options?.model, "opus")
        XCTAssertEqual(request.claude_options?.fast_mode, true)
    }

    func testClaudeUltracodeLocksXhighEffort() throws {
        let model = makeModel()
        model.selection = .stored("a")
        model.claudeUltracode = true
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(request.claude_options?.effort, "xhigh")
    }

    func testClaudeDebugReachesTheRequestOnlyWhenToggledOn() throws {
        let model = makeModel()
        model.selection = .stored("a")
        let untouched = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(
            untouched.claude_options?.debug, false,
            "Claude Code's --debug is opt-in now — an untouched wizard must not ask for it"
        )

        model.claudeDebug = true
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(request.claude_options?.debug, true)
    }

    func testClaudeEffortClampedWhenModelNarrows() throws {
        let model = makeModel()
        model.selection = .stored("a")
        model.claudeModel = "opus"
        model.claudeEffort = "max"
        let wide = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(wide.claude_options?.effort, "max")
        model.claudeModel = "haiku" // no longer supports "max"
        let narrowed = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(
            narrowed.claude_options?.effort, "high",
            "a stale effort unsupported by the new model clamps to a valid value"
        )
    }

    func testCodexUsesCliAndStateDefaults() throws {
        let model = makeModel()
        model.selection = .stored("a")
        let request = try XCTUnwrap(model.buildRequest(mode: .codex))
        XCTAssertEqual(request.cli, "codex")
        XCTAssertEqual(request.codex_options?.approval, "on-request")
        XCTAssertEqual(request.codex_options?.reasoning_effort, "high")
        XCTAssertEqual(request.codex_options?.sandbox, "workspace-write")
        XCTAssertNil(request.claude_options)
    }

    /// `refreshCliAvailability` against a stubbed agent advertising per-model
    /// Codex effort sets (`capabilities.cli.codex.model_details`).
    private func modelWithCodexDetails() async -> CreateSessionModel {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: #"""
        {"name":"agent","server_version":"1.0.0","protocol_versions":[7],"min_client_version":1,
         "capabilities":{"cli":{"codex":{
           "available":true,
           "models":["gpt-5.6-sol","gpt-5.4-mini"],
           "model_details":[
             {"id":"gpt-5.6-sol","efforts":["low","medium","ultra"],"default_effort":"low"},
             {"id":"gpt-5.4-mini","efforts":["medium","high"]}
           ]}}}}
        """#)
        let model = stubbedModel()
        await model.refreshCliAvailability()
        return model
    }

    func testRefreshReconcilesCatalogEffortAgainstDetailLessAgent() async {
        // A persisted catalog effort (e.g. "ultra" from a newer agent) aimed at
        // an agent advertising no model_details must drop to the default: that
        // agent may be a pre-catalog build whose wire schema still 422s efforts
        // outside the static vocabulary. In-vocabulary values stand.
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: #"""
        {"name":"agent","server_version":"1.0.0","protocol_versions":[7],"min_client_version":1,
         "capabilities":{"cli":{"codex":{"available":true}}}}
        """#)
        let model = stubbedModel()
        model.codexReasoningEffort = "ultra"
        await model.refreshCliAvailability()
        XCTAssertEqual(model.codexReasoningEffort, "high")

        StubURLProtocol.enqueue(status: 200, body: #"""
        {"name":"agent","server_version":"1.0.0","protocol_versions":[7],"min_client_version":1,
         "capabilities":{"cli":{"codex":{"available":true}}}}
        """#)
        model.codexReasoningEffort = "medium"
        await model.refreshCliAvailability()
        XCTAssertEqual(model.codexReasoningEffort, "medium")
    }

    func testRefreshKeepsCatalogEffortWhenDetailsAreAdvertised() async {
        // An agent advertising model_details provably accepts free-form
        // efforts — a restored catalog effort stands even when unadvertised
        // for the selected model (keep-selected).
        let model = await modelWithCodexDetails()
        StubURLProtocol.enqueue(status: 200, body: #"""
        {"name":"agent","server_version":"1.0.0","protocol_versions":[7],"min_client_version":1,
         "capabilities":{"cli":{"codex":{
           "available":true,
           "model_details":[{"id":"gpt-5.6-sol","efforts":["low"],"default_effort":"low"}]}}}}
        """#)
        model.codexReasoningEffort = "ultra"
        await model.refreshCliAvailability()
        XCTAssertEqual(model.codexReasoningEffort, "ultra")
    }

    func testRefreshClearsStaleDetailsWhenALaterProbeOmitsThem() async {
        // Agent restarted on an older build between probes: the retained
        // catalog would keep offering efforts (e.g. "ultra") the current
        // agent may reject, so a detail-less probe must clear it.
        let model = await modelWithCodexDetails()
        XCTAssertEqual(model.codexEfforts(for: "gpt-5.6-sol"), ["low", "medium", "ultra"])

        StubURLProtocol.enqueue(status: 200, body: #"""
        {"name":"agent","server_version":"1.0.0","protocol_versions":[7],"min_client_version":1,
         "capabilities":{"cli":{"codex":{"available":true}}}}
        """#)
        await model.refreshCliAvailability()

        XCTAssertEqual(model.codexEfforts(for: "gpt-5.6-sol"), CreateSessionModel.reasoningEfforts)
    }

    func testRefreshReconcilesEffortAgainstSelectedModelCatalogEntry() async {
        // Details advertised AND the selected model's entry omits the current
        // effort: the catalog says the model cannot serve it, so the probe
        // applies the same re-default setCodexModel uses on a switch.
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: #"""
        {"name":"agent","server_version":"1.0.0","protocol_versions":[7],"min_client_version":1,
         "capabilities":{"cli":{"codex":{
           "available":true,
           "model_details":[{"id":"gpt-5.6-luna","efforts":["low","medium","xhigh"],"default_effort":"medium"}]}}}}
        """#)
        let model = stubbedModel()
        model.codexModel = "gpt-5.6-luna"
        model.codexReasoningEffort = "ultra"

        await model.refreshCliAvailability()

        XCTAssertEqual(model.codexReasoningEffort, "medium")
    }

    func testCodexModelOptionsKeepAnUnlistedRestoredSelection() {
        // A model persisted before the fallback refresh (gpt-5.3-codex-spark)
        // is no longer in the list; without the append the Picker renders no
        // selection while buildRequest still submits the hidden value.
        let model = makeModel()
        XCTAssertEqual(model.codexModelOptions, CreateSessionModel.codexModelsFallback)

        model.codexModel = "gpt-5.3-codex-spark"
        XCTAssertEqual(
            model.codexModelOptions,
            CreateSessionModel.codexModelsFallback + ["gpt-5.3-codex-spark"]
        )

        // A listed selection appends nothing.
        model.codexModel = CreateSessionModel.codexModelsFallback[0]
        XCTAssertEqual(model.codexModelOptions, CreateSessionModel.codexModelsFallback)
    }

    func testCodexEffortsFallBackToStaticListWithoutDetails() {
        let model = makeModel()
        XCTAssertEqual(model.codexEfforts(for: "gpt-5.6-sol"), CreateSessionModel.reasoningEfforts)
        XCTAssertEqual(model.codexEfforts(for: nil), CreateSessionModel.reasoningEfforts)
    }

    func testCodexEffortsUseAdvertisedCatalogForSelectedModel() async {
        let model = await modelWithCodexDetails()
        XCTAssertEqual(model.codexModels, ["gpt-5.6-sol", "gpt-5.4-mini"])
        XCTAssertEqual(model.codexEfforts(for: "gpt-5.6-sol"), ["low", "medium", "ultra"])
        // Unknown model and the Default (nil) model both take the fallback —
        // their capabilities are unknown here.
        XCTAssertEqual(model.codexEfforts(for: "mystery"), CreateSessionModel.reasoningEfforts)
        XCTAssertEqual(model.codexEfforts(for: nil), CreateSessionModel.reasoningEfforts)
    }

    func testSetCodexModelResetsUnsupportedEffortToModelDefault() async {
        let model = await modelWithCodexDetails()
        model.codexReasoningEffort = "high"
        model.setCodexModel("gpt-5.6-sol")
        XCTAssertEqual(
            model.codexReasoningEffort, "low",
            "a stranded effort re-defaults to the catalog's per-model default"
        )

        // No catalog default → "high" wins when advertised.
        model.codexReasoningEffort = "minimal"
        model.setCodexModel("gpt-5.4-mini")
        XCTAssertEqual(model.codexReasoningEffort, "high")
    }

    func testSetCodexModelKeepsSupportedEffortAndUnknownModels() async {
        let model = await modelWithCodexDetails()
        model.codexReasoningEffort = "ultra"
        model.setCodexModel("gpt-5.6-sol")
        XCTAssertEqual(model.codexReasoningEffort, "ultra", "a still-supported effort survives")

        model.codexReasoningEffort = "minimal"
        model.setCodexModel("mystery")
        XCTAssertEqual(
            model.codexReasoningEffort, "minimal",
            "a model with no catalog entry keeps the effort — never downshift on a guess"
        )
        model.setCodexModel(nil)
        XCTAssertEqual(model.codexReasoningEffort, "minimal", "the Default model keeps it too")
    }

    func testAntigravityExclusivityAndAddDirs() throws {
        let model = makeModel()
        model.selection = .stored("a")
        model.setAntigravitySkipPermissions(true)
        model.setAntigravitySandbox(true) // must force skip-permissions off
        XCTAssertFalse(model.antigravitySkipPermissions)
        XCTAssertTrue(model.antigravitySandbox)
        model.antigravityAddDirs = "one\ntwo, three"
        let request = try XCTUnwrap(model.buildRequest(mode: .antigravity))
        XCTAssertEqual(request.cli, "antigravity")
        XCTAssertEqual(request.antigravity_options?.add_dirs, ["one", "two", "three"])
        XCTAssertEqual(request.antigravity_options?.sandbox, true)
        XCTAssertEqual(request.antigravity_options?.skip_permissions, false)
    }

    func testShellCarriesWorktreeOnTopLevel() async throws {
        // Shell worktree/branch ride the top-level request, but only for a
        // stored git project (worktree is git-gated).
        let model = await modelWithProjects(#"[{"name":"a","path":"/p","is_git":true}]"#)
        model.selectStoredProject("a")
        model.worktree = true
        model.branchName = "feat"
        let request = try XCTUnwrap(model.buildRequest(mode: .shell))
        XCTAssertNil(request.cli)
        XCTAssertNil(request.claude_options)
        XCTAssertEqual(request.project_name, "a")
        XCTAssertEqual(request.worktree, true)
        XCTAssertEqual(request.branch_name, "feat")
    }

    func testCustomTypeCarriesCliAndTopLevelWorktree() async throws {
        // Custom agent types launch with plain-terminal semantics plus the
        // `cli` discriminator — worktree/branch ride the top level exactly
        // like `.shell` (compare testShellCarriesWorktreeOnTopLevel).
        let model = await modelWithProjects(#"[{"name":"a","path":"/p","is_git":true}]"#)
        model.selectStoredProject("a")
        model.worktree = true
        model.branchName = "feat"
        let request = try XCTUnwrap(model.buildRequest(mode: .custom("ab12cd34ef56")))
        XCTAssertEqual(request.cli, "custom_ab12cd34ef56")
        XCTAssertNil(request.command, "the stored command is resolved relay-side — the client never sends it")
        XCTAssertNil(request.claude_options)
        XCTAssertNil(request.codex_options)
        XCTAssertNil(request.antigravity_options)
        XCTAssertEqual(request.project_name, "a")
        XCTAssertEqual(request.worktree, true)
        XCTAssertEqual(request.branch_name, "feat")
    }

    func testCustomPathForcesWorktreeOff() throws {
        let model = makeModel()
        model.setCustomPath("/abs/path")
        model.worktree = true
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(request.cwd, "/abs/path")
        XCTAssertNil(request.project_name)
        XCTAssertEqual(request.worktree, false)
        XCTAssertEqual(request.claude_options?.worktree, false)
    }

    func testCustomPathTrimmedInRequest() throws {
        let model = makeModel()
        model.setCustomPath("  /abs/path  ")
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(request.cwd, "/abs/path")
    }

    func testNonGitStoredProjectDropsWorktree() async throws {
        let model = await modelWithProjects(#"[{"name":"plain","path":"/p","is_git":false}]"#)
        model.selectStoredProject("plain")
        model.worktree = true
        model.branchName = "x"
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertFalse(model.showsGitOptions)
        XCTAssertEqual(request.claude_options?.worktree, false, "a non-git project must not send a stale worktree toggle")
        XCTAssertNil(request.claude_options?.branch_name)
    }

    func testGitStoredProjectKeepsWorktree() async throws {
        let model = await modelWithProjects(#"[{"name":"repo","path":"/p","is_git":true}]"#)
        model.selectStoredProject("repo")
        model.worktree = true
        model.branchName = "feat"
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
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

    func testOnProjectCreatedSelectsAndGatesGit() {
        let model = makeModel()

        let gitProject = ProjectInfo(name: "repo", path: "/p", is_git: true)
        model.worktree = true
        model.branchName = "feat"
        model.onProjectCreated(gitProject)
        XCTAssertEqual(model.selection, .stored("repo"))
        XCTAssertTrue(model.projects.contains(gitProject), "the created project is merged into the wizard snapshot")
        XCTAssertTrue(model.showsGitOptions)
        XCTAssertTrue(model.worktree, "a git project keeps the worktree toggle")

        let plainProject = ProjectInfo(name: "plain", path: "/q", is_git: false)
        model.onProjectCreated(plainProject)
        XCTAssertEqual(model.selection, .stored("plain"))
        XCTAssertFalse(model.showsGitOptions)
        XCTAssertFalse(model.worktree, "a non-git project resets the stale worktree toggle")
        XCTAssertEqual(model.branchName, "")
    }

    func testLateLoadPreservesInlineCreatedProject() async {
        StubURLProtocol.reset()
        let model = stubbedModel()

        // A project is created inline before the initial load() response lands.
        let created = ProjectInfo(name: "fresh", path: "/p", is_git: true)
        model.onProjectCreated(created)

        // The late load() snapshot predates the create and omits it.
        StubURLProtocol.enqueue(status: 200, body: #"[{"name":"old","path":"/o","is_git":false}]"#)
        await model.load()

        XCTAssertTrue(model.projects.contains(created), "a stale load must not drop the inline-created project")
        XCTAssertEqual(model.selection, .stored("fresh"))
        XCTAssertTrue(model.showsGitOptions, "Git options stay available for the created Git repo")
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
            model: "sonnet", effort: "low", fastMode: false, ultracode: false, appendSystemPrompt: "sys",
            skipPermissions: true, noMemory: true
        ))
        prefs.setLastUsedCodexOptions(CodexLaunchPrefs(
            model: "gpt-5.4", sandbox: "read-only", approval: "never", fullAuto: true, reasoningEffort: "medium"
        ))
        let model = makeModel(prefs: prefs)
        XCTAssertEqual(model.claudeModel, "sonnet")
        XCTAssertEqual(model.claudeEffort, "low")
        XCTAssertEqual(model.claudeAppendSystemPrompt, "sys")
        XCTAssertTrue(model.skipPermissions, "the persisted toggle must pre-set the sheet")
        XCTAssertTrue(model.noMemory)
        XCTAssertEqual(model.codexModel, "gpt-5.4")
        XCTAssertEqual(model.codexApproval, "never")
        XCTAssertTrue(model.codexFullAuto)
    }

    func testCreatePersistsSkipPermissionsAndNoMemory() async {
        StubURLProtocol.reset()
        let prefs = isolatedPrefs()
        let model = stubbedModel(prefs: prefs)
        model.selection = .stored("a")
        model.mode = .standard
        model.skipPermissions = true
        model.noMemory = true
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-new","state":"starting","created_at":"t"}"#
        )

        await model.create()

        XCTAssertNil(model.error)
        let saved = prefs.getLastUsedClaudeOptions()
        XCTAssertEqual(saved?.skipPermissions, true, "the toggles now survive into the next launch")
        XCTAssertEqual(saved?.noMemory, true)
    }

    func testCreateRoundTripsClaudeDebugThroughItsOwnPrefsBucket() async {
        StubURLProtocol.reset()
        let prefs = isolatedPrefs()
        let model = stubbedModel(prefs: prefs)
        model.selection = .stored("a")
        model.mode = .standard
        model.claudeDebug = true
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-new","state":"starting","created_at":"t"}"#
        )

        await model.create()

        XCTAssertNil(model.error)
        XCTAssertEqual(prefs.getLastUsedClaudeOptions()?.debug, true)
        XCTAssertNil(
            prefs.getLastUsedAutoScanOptions(),
            "a Claude Code launch never writes the Auto Scan bucket"
        )
        let reopened = makeModel(prefs: prefs)
        XCTAssertTrue(reopened.claudeDebug, "the next wizard opens on the remembered choice")
        XCTAssertFalse(reopened.autoScanClaudeDebug)
    }

    // MARK: - Recent custom paths

    func testSelectCustomPathLoadsRecentsAndLeavesTheEmptyDraftUnflagged() {
        let prefs = isolatedPrefs()
        prefs.addRecentCustomPath(agentKey: harnessAgentKey, path: "/srv/app")
        let model = makeModel(prefs: prefs)
        model.selectStoredProject("alpha")
        model.worktree = true
        model.branchName = "feat"

        model.selectCustomPath()

        XCTAssertEqual(model.selection, .customPath(""))
        XCTAssertEqual(model.customPathDraft, "")
        XCTAssertNil(
            model.customPathError,
            "an untouched field must not render \"Path is required\" the moment the section appears"
        )
        XCTAssertFalse(model.worktree, "the stored project's git options do not carry into an ad-hoc path")
        XCTAssertEqual(model.branchName, "")
        XCTAssertEqual(
            model.recentCustomPaths, ["/srv/app"],
            "recents resolve through the agent-url fallback when there is no agent id"
        )
    }

    func testReselectingCustomPathRefreshesRecentsWithoutWipingTheDraft() {
        let prefs = isolatedPrefs()
        let model = makeModel(prefs: prefs)
        model.selectCustomPath()
        model.setCustomPath("/typed/path")
        prefs.addRecentCustomPath(agentKey: harnessAgentKey, path: "/added/later")

        model.selectCustomPath()

        XCTAssertEqual(model.customPathDraft, "/typed/path", "the row stays on screen, so a second tap must not clear typed text")
        XCTAssertEqual(model.selection, .customPath("/typed/path"))
        XCTAssertEqual(model.recentCustomPaths, ["/added/later"], "recents still refresh")
    }

    func testPickRecentCustomPathFillsTheFieldAndRunsValidation() {
        let prefs = isolatedPrefs()
        prefs.addRecentCustomPath(agentKey: harnessAgentKey, path: "/srv/app")
        let model = makeModel(prefs: prefs)
        model.selectCustomPath()

        model.pickRecentCustomPath("/srv/app")

        XCTAssertEqual(model.customPathDraft, "/srv/app")
        XCTAssertEqual(model.selection, .customPath("/srv/app"))
        XCTAssertNil(model.customPathError)
        XCTAssertTrue(model.canContinueProject, "a tapped recent unblocks the step exactly like typed text")
    }

    func testRemoveRecentCustomPathClearsItFromStateAndStore() {
        let prefs = isolatedPrefs()
        prefs.addRecentCustomPath(agentKey: harnessAgentKey, path: "/a")
        prefs.addRecentCustomPath(agentKey: harnessAgentKey, path: "/b")
        let model = makeModel(prefs: prefs)
        model.selectCustomPath()
        XCTAssertEqual(model.recentCustomPaths, ["/b", "/a"])

        model.removeRecentCustomPath("/b")

        XCTAssertEqual(model.recentCustomPaths, ["/a"], "the swiped row disappears without re-entering the step")
        XCTAssertEqual(prefs.getRecentCustomPaths(agentKey: harnessAgentKey), ["/a"], "and stays gone next launch")
    }

    func testCreateWithCustomPathRecordsTheRecentButNeverTheLastUsedProject() async {
        StubURLProtocol.reset()
        let prefs = isolatedPrefs()
        prefs.setLastUsedProjectName("alpha")
        let model = stubbedModel(prefs: prefs)
        model.mode = .standard
        model.selectCustomPath()
        model.setCustomPath("  /srv/app  ")
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-new","state":"starting","created_at":"t"}"#
        )

        await model.create()

        XCTAssertNil(model.error)
        XCTAssertEqual(
            prefs.getRecentCustomPaths(agentKey: harnessAgentKey), ["/srv/app"],
            "the trimmed launch directory is recorded under the agent scope key"
        )
        XCTAssertEqual(model.recentCustomPaths, ["/srv/app"], "the open wizard picks it up immediately")
        XCTAssertEqual(
            prefs.getLastUsedProjectName(), "alpha",
            "an ad-hoc path must never displace the last-used project"
        )
    }

    func testCreateWithoutAnAgentScopeKeySkipsRecentsAndStillSucceeds() async {
        StubURLProtocol.reset()
        let defaults = UserDefaults(suiteName: "cw-\(UUID().uuidString)")!
        let prefs = PreferencesStore(defaults: defaults)
        let model = stubbedModel(prefs: prefs, tokenStore: unscopedTokenStore())
        model.mode = .standard
        model.selectCustomPath()
        XCTAssertEqual(model.recentCustomPaths, [], "no scope key means nothing to load")
        model.setCustomPath("/srv/app")
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-new","state":"starting","created_at":"t"}"#
        )

        await model.create()

        XCTAssertNil(model.error, "a missing scope key must not fail the launch")
        let recentKeys = defaults.dictionaryRepresentation().keys.filter {
            $0.hasPrefix(PreferencesStore.recentCustomPathsPrefix)
        }
        XCTAssertEqual(recentKeys, [], "and must not write an unscoped bucket")
    }

    func testCreateRecordsTheLaunchedPathNotOneTypedWhileTheCreateWasInFlight() async {
        StubURLProtocol.reset()
        let prefs = isolatedPrefs()
        let model = stubbedModel(prefs: prefs)
        model.mode = .standard
        model.selectCustomPath()
        model.setCustomPath("/srv/launched")
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-new","state":"starting","created_at":"t"}"#
        )

        // Back is not gated on `submitting`, so the field stays editable while
        // the POST is in flight; hold the response until the edit has landed.
        let released = DispatchSemaphore(value: 0)
        StubURLProtocol.onRequest = { _ in
            StubURLProtocol.onRequest = nil
            Task { @MainActor in
                model.setCustomPath("/srv/edited")
                released.signal()
            }
            XCTAssertEqual(released.wait(timeout: .now() + 2), .success, "the mid-flight edit never landed")
        }

        await model.create()

        XCTAssertNil(model.error)
        XCTAssertEqual(
            prefs.getRecentCustomPaths(agentKey: harnessAgentKey), ["/srv/launched"],
            "recents record the directory the session was launched in, never a later edit"
        )
        XCTAssertEqual(model.recentCustomPaths, ["/srv/launched"])
        XCTAssertEqual(model.customPathDraft, "/srv/edited", "the in-flight edit itself is left alone")
    }

    func testCreateOfAStoredProjectIgnoresACustomPathEnteredMidFlight() async {
        StubURLProtocol.reset()
        let prefs = isolatedPrefs()
        let model = stubbedModel(prefs: prefs)
        model.mode = .standard
        model.selectStoredProject("alpha")
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-new","state":"starting","created_at":"t"}"#
        )

        let released = DispatchSemaphore(value: 0)
        StubURLProtocol.onRequest = { _ in
            StubURLProtocol.onRequest = nil
            Task { @MainActor in
                model.selectCustomPath()
                model.setCustomPath("/srv/never-launched")
                released.signal()
            }
            XCTAssertEqual(released.wait(timeout: .now() + 2), .success, "the mid-flight selection change never landed")
        }

        await model.create()

        XCTAssertNil(model.error)
        XCTAssertEqual(
            prefs.getLastUsedProjectName(), "alpha",
            "the launched stored project is what the wizard remembers, even though the selection moved on"
        )
        XCTAssertEqual(
            prefs.getRecentCustomPaths(agentKey: harnessAgentKey), [],
            "a directory that was never launched must not become a recent"
        )
    }

    func testCreateNeverRecordsAPathTheWizardWouldReject() async {
        StubURLProtocol.reset()
        let prefs = isolatedPrefs()
        let model = stubbedModel(prefs: prefs)
        model.mode = .standard
        model.selectCustomPath()
        // `setCustomPath` assigns `selection` even when it flags the draft, so
        // an invalid path is reachable at the create call.
        model.setCustomPath("relative/path")
        XCTAssertEqual(model.customPathError, "Path must be absolute")
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-new","state":"starting","created_at":"t"}"#
        )

        await model.create()

        XCTAssertNil(model.error)
        XCTAssertEqual(
            prefs.getRecentCustomPaths(agentKey: harnessAgentKey), [],
            "a rejected path must not become a row that can never be launched"
        )
    }

    // MARK: - Custom agent types: validation / sanitization statics

    func testValidateCustomTypeMessagesAndOrder() {
        XCTAssertNil(CreateSessionModel.validateCustomType(name: "Aider", command: "aider --model sonnet"))
        XCTAssertEqual(CreateSessionModel.validateCustomType(name: "", command: "aider"), "Name is required")
        XCTAssertEqual(
            CreateSessionModel.validateCustomType(name: String(repeating: "a", count: 61), command: "aider"),
            "Name must be at most 60 characters"
        )
        XCTAssertEqual(CreateSessionModel.validateCustomType(name: "Aider", command: ""), "Command is required")
        XCTAssertEqual(
            CreateSessionModel.validateCustomType(name: "Aider", command: String(repeating: "x", count: 1001)),
            "Command must be at most 1000 characters"
        )
        XCTAssertNil(
            CreateSessionModel.validateCustomType(name: "Aider", command: String(repeating: "x", count: 1000)),
            "the limit is inclusive"
        )
        XCTAssertEqual(
            CreateSessionModel.validateCustomType(name: "", command: ""), "Name is required",
            "name errors win over command errors (Android message order)"
        )
    }

    func testValidateCustomTypeCountsUnicodeCodePointsNotGraphemes() {
        // "👍🏽" is one grapheme but two Unicode scalars, so 31 of them pass a
        // grapheme count (31 ≤ 60) yet exceed the server's 60-code-point
        // limit (62) — the validator must reject what the server rejects.
        let emojiName = String(repeating: "👍🏽", count: 31)
        XCTAssertLessThanOrEqual(emojiName.count, 60)
        XCTAssertEqual(
            CreateSessionModel.validateCustomType(name: emojiName, command: "aider"),
            "Name must be at most 60 characters"
        )
        XCTAssertNil(
            CreateSessionModel.validateCustomType(name: String(repeating: "👍🏽", count: 30), command: "aider"),
            "exactly 60 code points is accepted"
        )
    }

    func testSanitizeCommandPunctuationUndoesSmartSubstitutions() {
        XCTAssertEqual(
            CreateSessionModel.sanitizeCommandPunctuation(
                "\u{201C}aider\u{201D} \u{2014}model \u{2018}sonnet\u{2019} \u{2013}x \u{201E}q\u{201A}"
            ),
            #""aider" --model 'sonnet' -x "q'"#
        )
        XCTAssertEqual(
            CreateSessionModel.sanitizeCommandPunctuation("aider --model sonnet"),
            "aider --model sonnet",
            "already-clean input passes through unchanged"
        )
    }

    func testEditorSaveEnablementRequiresBothFieldsNonBlank() {
        XCTAssertFalse(CustomAgentTypeEditorSheet.isSaveEnabled(name: "  ", command: "aider"))
        XCTAssertFalse(CustomAgentTypeEditorSheet.isSaveEnabled(name: "Aider", command: " \n "))
        XCTAssertTrue(CustomAgentTypeEditorSheet.isSaveEnabled(name: " Aider ", command: " aider "))
    }

    // MARK: - Custom agent types: mode card metadata

    func testCustomModeTitleAndReasonFallBackWhenTypeVanishes() async {
        let typesRepo = makeTypesRepo()
        await typesRepo.applyUpserted(makeCustomAgentType())
        let model = makeModel(typesRepo: typesRepo)

        XCTAssertEqual(model.modeTitle(.custom("ab12cd34ef56")), "Aider")
        XCTAssertEqual(model.reason(for: .custom("ab12cd34ef56")), "aider --model sonnet")
        XCTAssertEqual(
            model.modeTitle(.custom("ffffffffffff")), "Terminal session",
            "a remotely-deleted type keeps the wizard usable with the terminal fallback"
        )
        XCTAssertEqual(model.reason(for: .custom("ffffffffffff")), "Runs your saved launch command")
    }

    // MARK: - Custom agent types: save flow

    func testSaveCustomTypeValidationFailsInlineWithoutNetwork() async {
        StubURLProtocol.reset()
        let model = makeModel()
        model.typeEditorTarget = .create

        await model.saveCustomType(name: "   ", command: "aider")

        XCTAssertEqual(model.typeEditorError, "Name is required")
        XCTAssertNotNil(model.typeEditorTarget, "the editor stays open on validation failure")
        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "invalid input never reaches the wire")
    }

    func testSaveCustomTypeCreateSuccessDismissesAndUpserts() async throws {
        StubURLProtocol.reset()
        let typesRepo = makeTypesRepo()
        let model = stubbedModel(typesRepo: typesRepo)
        model.typeEditorTarget = .create
        StubURLProtocol.enqueue(
            status: 201,
            body: #"{"id":"ab12cd34ef56","name":"Aider","command":"aider --model sonnet","created_at":"c","updated_at":"u"}"#
        )

        await model.saveCustomType(name: " Aider ", command: " aider --model sonnet ")

        XCTAssertNil(model.typeEditorError)
        XCTAssertNil(model.typeEditorTarget, "the sheet dismisses only on success")
        XCTAssertEqual(typesRepo.currentTypes.map(\.id), ["ab12cd34ef56"])
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "POST")
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["name": "Aider", "command": "aider --model sonnet"], "values are trimmed before the wire")
    }

    func testSaveCustomTypeEditSanitizesPastedPunctuationAndPatchesBothFields() async throws {
        StubURLProtocol.reset()
        let typesRepo = makeTypesRepo()
        await typesRepo.applyUpserted(makeCustomAgentType())
        let model = stubbedModel(typesRepo: typesRepo)
        model.typeEditorTarget = .edit(makeCustomAgentType())
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"id":"ab12cd34ef56","name":"Aider","command":"aider --model \"sonnet\"","created_at":"c","updated_at":"u2"}"#
        )

        // Pasted text bypasses keyboard traits — the save path must undo the
        // smart punctuation the live editor would have caught.
        await model.saveCustomType(name: "Aider", command: "aider \u{2014}model \u{201C}sonnet\u{201D}")

        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "PATCH")
        XCTAssertEqual(StubURLProtocol.requests.first?.url?.path, "/api/custom-agent-types/ab12cd34ef56")
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(
            body, ["name": "Aider", "command": #"aider --model "sonnet""#],
            "an edit always sends both fields (never an empty PATCH) with sanitized punctuation"
        )
        XCTAssertNil(model.typeEditorTarget)
    }

    func testSaveCustomTypeConflictSurfacesInlineAndKeepsDraft() async {
        StubURLProtocol.reset()
        let model = stubbedModel()
        model.typeEditorTarget = .create
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":"A custom agent type with this name already exists"}"#)

        await model.saveCustomType(name: "Aider", command: "aider")

        XCTAssertEqual(model.typeEditorError, "A custom agent type with this name already exists")
        XCTAssertNotNil(model.typeEditorTarget, "a failed save keeps the sheet and the draft")
    }

    func testSaveCustomTypeNonDetailErrorFallsBackToHTTPCode() async {
        StubURLProtocol.reset()
        let model = stubbedModel()
        model.typeEditorTarget = .create
        StubURLProtocol.enqueue(status: 500, body: "boom")

        await model.saveCustomType(name: "Aider", command: "aider")

        XCTAssertEqual(model.typeEditorError, "Couldn't save (HTTP 500)")
        XCTAssertNotNil(model.typeEditorTarget)
    }

    // MARK: - Custom agent types: delete flow

    func testConfirmDeleteType403CountsAsSuccessAndRefreshes() async {
        StubURLProtocol.reset()
        let typesRepo = makeTypesRepo()
        await typesRepo.applyUpserted(makeCustomAgentType())
        let model = stubbedModel(typesRepo: typesRepo)
        model.typeDeleteTarget = makeCustomAgentType()
        StubURLProtocol.enqueue(status: 403, body: #"{"detail":"Custom agent type not found"}"#)
        StubURLProtocol.enqueue(status: 200, body: "[]")

        await model.confirmDeleteType(makeCustomAgentType())

        XCTAssertNil(model.error, "an already-gone type (repeat delete / other device) is success, not an error")
        XCTAssertEqual(
            model.typeDeleteTarget?.id, "ab12cd34ef56",
            "the model leaves the alert target alone — the alert's presentation binding clears it"
        )
        XCTAssertEqual(
            StubURLProtocol.requests.compactMap(\.httpMethod), ["DELETE", "GET"],
            "the 403 triggers a best-effort refresh so the cards converge"
        )
        XCTAssertTrue(typesRepo.currentTypes.isEmpty)
    }

    func testConfirmDeleteTypeFailureSurfacesToastAndKeepsCache() async {
        StubURLProtocol.reset()
        let typesRepo = makeTypesRepo()
        await typesRepo.applyUpserted(makeCustomAgentType())
        let model = stubbedModel(typesRepo: typesRepo)
        model.typeDeleteTarget = makeCustomAgentType()
        StubURLProtocol.enqueue(status: 500, body: "boom")

        await model.confirmDeleteType(makeCustomAgentType())

        XCTAssertTrue(model.error?.hasPrefix("Failed to delete agent type") ?? false)
        XCTAssertEqual(
            model.typeDeleteTarget?.id, "ab12cd34ef56",
            "the model leaves the alert target alone — nilling it from the async flow could tear down a second type's freshly-raised confirmation"
        )
        XCTAssertEqual(typesRepo.currentTypes.map(\.id), ["ab12cd34ef56"], "the delete failed — the card stays")
    }

    func testConfirmDeleteTypeRepeatConfirmOfSameInFlightTypeIsNoOp() async {
        StubURLProtocol.reset()
        let typesRepo = makeTypesRepo()
        await typesRepo.applyUpserted(makeCustomAgentType())
        let model = stubbedModel(typesRepo: typesRepo)
        model.typeDeleteTarget = makeCustomAgentType()
        StubURLProtocol.enqueue(status: 200, body: "{}")

        // Hold the first DELETE's response until a repeat confirm of the same
        // type has run — the alert auto-dismisses on tap, so this re-confirm
        // window is reachable in the UI.
        let released = DispatchSemaphore(value: 0)
        StubURLProtocol.onRequest = { request in
            guard request.httpMethod == "DELETE" else { return }
            StubURLProtocol.onRequest = nil
            Task { @MainActor in
                await model.confirmDeleteType(makeCustomAgentType())
                released.signal()
            }
            XCTAssertEqual(released.wait(timeout: .now() + 2), .success, "the repeat confirm never completed")
        }

        await model.confirmDeleteType(makeCustomAgentType())

        XCTAssertEqual(
            StubURLProtocol.requests.filter { $0.httpMethod == "DELETE" }.count, 1,
            "a repeat confirm of a type whose DELETE is already in flight must not fire a second call"
        )
        XCTAssertNil(model.error)
        XCTAssertTrue(model.deletesInFlightTypeIds.isEmpty, "the guard entry is released once the DELETE settles")
        XCTAssertTrue(typesRepo.currentTypes.isEmpty)
        XCTAssertEqual(
            model.typeDeleteTarget?.id, "ab12cd34ef56",
            "the model must not clear the alert target — the presentation binding owns it"
        )
    }

    func testConfirmDeleteTypeSecondTypeWhileFirstInFlightBothFire() async {
        StubURLProtocol.reset()
        let typesRepo = makeTypesRepo()
        await typesRepo.applyUpserted(makeCustomAgentType())
        await typesRepo.applyUpserted(makeCustomAgentType(id: "cd34ef56ab12", name: "Goose", command: "goose"))
        let model = stubbedModel(typesRepo: typesRepo)
        StubURLProtocol.enqueue(status: 200, body: "{}")
        StubURLProtocol.enqueue(status: 200, body: "{}")

        // The alert dismisses on tap, so a second type can be confirmed while
        // the first DELETE still awaits; hold the first response until the
        // second confirm is provably in flight (a single-boolean guard would
        // silently drop that second confirmed destructive action).
        let released = DispatchSemaphore(value: 0)
        StubURLProtocol.onRequest = { request in
            guard request.url?.lastPathComponent == "ab12cd34ef56" else { return }
            StubURLProtocol.onRequest = nil
            Task { @MainActor in
                let second = Task { @MainActor in
                    await model.confirmDeleteType(makeCustomAgentType(id: "cd34ef56ab12", name: "Goose", command: "goose"))
                }
                let deadline = ContinuousClock.now.advanced(by: .seconds(2))
                while !model.deletesInFlightTypeIds.contains("cd34ef56ab12"), ContinuousClock.now < deadline {
                    try? await Task.sleep(for: .milliseconds(2))
                }
                released.signal()
                await second.value
            }
            XCTAssertEqual(released.wait(timeout: .now() + 2), .success, "the second confirm never became in-flight")
        }

        await model.confirmDeleteType(makeCustomAgentType())
        await waitUntil { StubURLProtocol.requests.count == 2 && model.deletesInFlightTypeIds.isEmpty }

        XCTAssertEqual(
            Set(StubURLProtocol.requests.compactMap { $0.url?.lastPathComponent }),
            ["ab12cd34ef56", "cd34ef56ab12"],
            "a second type confirmed while the first DELETE is in flight must not be silently dropped"
        )
        XCTAssertNil(model.error)
        XCTAssertTrue(typesRepo.currentTypes.isEmpty, "both confirmed deletes reach the server and apply")
    }

    // MARK: - Custom agent types: launch errors

    func testCreateCustomModeAgentUpdateRequired409() async {
        StubURLProtocol.reset()
        let model = stubbedModel()
        model.selection = .stored("a")
        model.mode = .custom("ab12cd34ef56")
        StubURLProtocol.enqueue(status: 409, body: #"{"agent_update_required": true, "detail": "agent protocol too old"}"#)

        await model.create()

        XCTAssertEqual(model.error, "Update your agent to launch custom agent types")
        XCTAssertFalse(model.submitting)
    }

    func testCreateCustomModeDeletedType404RefreshesTypes() async {
        StubURLProtocol.reset()
        let typesRepo = makeTypesRepo()
        await typesRepo.applyUpserted(makeCustomAgentType())
        let model = stubbedModel(typesRepo: typesRepo)
        model.selection = .stored("a")
        model.mode = .custom("ab12cd34ef56")
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Custom agent type not found"}"#)
        StubURLProtocol.enqueue(status: 200, body: "[]")

        await model.create()

        XCTAssertEqual(model.error, "This agent type was deleted")
        XCTAssertTrue(typesRepo.currentTypes.isEmpty, "the stale card disappears via the best-effort refresh")
    }

    func testCreateCustomModeOther404FallsThroughToGenericError() async {
        // A concurrently-deleted *project* also 404s — matching on the status
        // alone mislabeled it as a deleted agent type (a shipped-then-fixed
        // Android regression). Only the exact detail string routes to the
        // deleted-type message.
        StubURLProtocol.reset()
        let model = stubbedModel()
        model.selection = .stored("a")
        model.mode = .custom("ab12cd34ef56")
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Project not found"}"#)

        await model.create()

        XCTAssertTrue(model.error?.hasPrefix("Failed to create session") ?? false)
        XCTAssertEqual(StubURLProtocol.requests.count, 1, "no type refresh for a non-type 404")
    }
}
