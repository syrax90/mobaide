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

    func testClaudeFocusModeDefaultsOnAndSendsAnExplicitFalse() throws {
        let model = makeModel()
        model.selection = .stored("a")
        let untouched = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(untouched.claude_options?.focus_mode, true)

        model.focusMode = false
        let request = try XCTUnwrap(model.buildRequest(mode: .standard))
        XCTAssertEqual(
            request.claude_options?.focus_mode, false,
            "off must reach the agent as false, never as a nil the encoder would drop"
        )
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

    // MARK: - Codex chat view

    /// `refreshCliAvailability` against a stubbed agent whose
    /// `capabilities.chat` is `chatJSON`.
    private func modelWithChat(_ chatJSON: String) async -> CreateSessionModel {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: """
        {"name":"agent","server_version":"1.0.0","protocol_versions":[7],"min_client_version":1,
         "capabilities":{"cli":{"codex":{"available":true}},"chat":\(chatJSON)}}
        """)
        let model = stubbedModel()
        await model.refreshCliAvailability()
        return model
    }

    func testCodexOpensTheChatViewUnlessTerminalUIIsOn() throws {
        let model = makeModel()
        model.selection = .stored("a")
        XCTAssertEqual(try XCTUnwrap(model.buildRequest(mode: .codex)).ui, "chat")

        model.codexTerminalUI = true

        XCTAssertEqual(try XCTUnwrap(model.buildRequest(mode: .codex)).ui, "terminal")
    }

    func testCodexWithoutChatOnThePCOpensTheTerminal() async throws {
        let model = await modelWithChat(#"{"claude":{"resume":true}}"#)
        model.selection = .stored("a")
        model.mode = .codex

        let request = try XCTUnwrap(model.buildRequest(mode: .codex))

        XCTAssertEqual(request.ui, "terminal")
        XCTAssertEqual(model.codexChatUnavailableReason, "The chat view needs Node.js on your PC.")
        XCTAssertFalse(model.canContinueSession)
    }

    func testCodexContinueRequestUsesTheCodexOptionsWithoutAWorktree() {
        let model = makeModel()
        model.mode = .codex
        model.selection = .stored("a")
        model.codexSandbox = "read-only"
        let stored = ChatStoredSession(
            session_id: "external-2222", cwd: "/srv/other", title: nil, updated_at: nil, open_session_id: nil
        )

        let request = model.continueRequest(for: stored)

        XCTAssertEqual(request.cli, "codex")
        XCTAssertEqual(request.ui, "chat")
        XCTAssertEqual(request.chat_resume_id, "external-2222")
        XCTAssertEqual(request.cwd, "/srv/other")
        XCTAssertNil(request.project_name)
        XCTAssertNil(request.claude_options)
        XCTAssertEqual(request.codex_options?.sandbox, "read-only")
        XCTAssertEqual(request.codex_options?.worktree, false)
        XCTAssertNil(request.codex_options?.branch_name)
    }

    func testEachCodexPresetSetsItsPairAndIsReportedBack() {
        let model = makeModel()

        model.applyCodexPreset(.fullAccess)

        XCTAssertEqual(model.codexPreset, .fullAccess)
        XCTAssertEqual(model.codexSandbox, "danger-full-access")
        XCTAssertEqual(model.codexApproval, "never")
        XCTAssertTrue(model.codexFullAuto)

        model.applyCodexPreset(.readOnly)

        XCTAssertEqual(model.codexPreset, .readOnly)
        XCTAssertEqual(model.codexSandbox, "read-only")
        XCTAssertEqual(model.codexApproval, "on-request")
        XCTAssertFalse(model.codexFullAuto, "leaving Full access turns --yolo off")

        model.applyCodexPreset(.auto)

        XCTAssertEqual(model.codexPreset, .auto)
        XCTAssertEqual(model.codexSandbox, "workspace-write")
        XCTAssertEqual(model.codexApproval, "on-request")
    }

    func testCodexCreateWithoutChatOnThePCPointsToTerminalUI() async {
        StubURLProtocol.reset()
        let model = stubbedModel()
        model.selection = .stored("a")
        model.mode = .codex
        StubURLProtocol.enqueue(
            status: 503,
            body: #"{"detail":{"detail":"The chat view needs Node.js 22 or later on the PC.","chat_unavailable":true}}"#
        )

        await model.create()

        XCTAssertEqual(
            model.error,
            "The chat view needs Node.js 22 or later on the PC. Turn on Terminal UI under Advanced to use the classic terminal."
        )
        XCTAssertFalse(model.submitting)
    }

    // MARK: - Antigravity chat view

    func testCreateSendsAndRemembersTheAntigravityTerminalUIChoice() async throws {
        StubURLProtocol.reset()
        let prefs = isolatedPrefs()
        let model = stubbedModel(prefs: prefs)
        model.selection = .stored("a")
        model.mode = .antigravity
        model.antigravityTerminalUI = true
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-new","state":"starting","created_at":"t"}"#
        )

        await model.create()

        XCTAssertNil(model.error)
        let body = try XCTUnwrap(try createBodies().last)
        XCTAssertEqual(body["cli"] as? String, "antigravity")
        XCTAssertEqual(body["ui"] as? String, "terminal")
        XCTAssertEqual(prefs.getLastUsedAntigravityOptions()?.terminalUI, true)
    }

    // MARK: - OpenCode

    /// `refreshCliAvailability` against a stubbed agent whose `capabilities`
    /// object is `capabilitiesJSON`.
    private func modelWithCapabilities(_ capabilitiesJSON: String) async -> CreateSessionModel {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: """
        {"name":"agent","server_version":"1.0.0","protocol_versions":[7],"min_client_version":1,
         "capabilities":\(capabilitiesJSON)}
        """)
        let model = stubbedModel()
        await model.refreshCliAvailability()
        return model
    }

    func testOpenCodeRequestAsksBeforeEachPermissionUnlessAutoApproveIsOn() throws {
        let model = makeModel()
        model.selection = .stored("a")

        let untouched = try XCTUnwrap(model.buildRequest(mode: .opencode))

        XCTAssertEqual(untouched.cli, "opencode")
        XCTAssertEqual(untouched.ui, "chat")
        XCTAssertEqual(untouched.opencode_options?.auto_approve, false)
        XCTAssertNil(untouched.claude_options)
        XCTAssertNil(untouched.codex_options)
        XCTAssertNil(untouched.antigravity_options)

        model.opencodeAutoApprove = true
        model.opencodeTerminalUI = true
        let dialled = try XCTUnwrap(model.buildRequest(mode: .opencode))

        XCTAssertEqual(dialled.opencode_options?.auto_approve, true)
        XCTAssertEqual(dialled.ui, "terminal")
    }

    func testOpenCodeCarriesWorktreeInItsOwnOptionsBlock() async throws {
        let model = await modelWithProjects(#"[{"name":"a","path":"/p","is_git":true}]"#)
        model.selectStoredProject("a")
        model.worktree = true
        model.branchName = "feat"

        let request = try XCTUnwrap(model.buildRequest(mode: .opencode))

        XCTAssertEqual(request.project_name, "a")
        XCTAssertEqual(request.opencode_options?.worktree, true)
        XCTAssertEqual(request.opencode_options?.branch_name, "feat")
        XCTAssertEqual(request.worktree, false, "a managed CLI's worktree never rides the top level")
        XCTAssertNil(request.branch_name)
    }

    func testCreateSendsAndRemembersTheOpenCodeChoices() async throws {
        StubURLProtocol.reset()
        let prefs = isolatedPrefs()
        let model = stubbedModel(prefs: prefs)
        model.selection = .stored("a")
        model.mode = .opencode
        model.opencodeAutoApprove = true
        model.opencodeTerminalUI = true
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-new","state":"starting","created_at":"t"}"#
        )

        await model.create()

        XCTAssertNil(model.error)
        let body = try XCTUnwrap(try createBodies().last)
        XCTAssertEqual(body["cli"] as? String, "opencode")
        XCTAssertEqual(body["ui"] as? String, "terminal")
        let options = try XCTUnwrap(body["opencode_options"] as? [String: Any])
        XCTAssertEqual(options["auto_approve"] as? Bool, true)
        XCTAssertNil(body["claude_options"])
        XCTAssertEqual(prefs.getLastUsedOpenCodeOptions(), OpenCodeLaunchPrefs(autoApprove: true, terminalUI: true))
        XCTAssertNil(prefs.getLastUsedClaudeOptions(), "an OpenCode launch writes no other agent's bucket")

        let next = makeModel(prefs: prefs)
        XCTAssertTrue(next.opencodeAutoApprove, "the saved choices pre-set the next wizard")
        XCTAssertTrue(next.opencodeTerminalUI)
        XCTAssertFalse(next.antigravitySkipPermissions)
    }

    func testConnectRequestIsABareTerminalSignInWhateverTheWizardHolds() async throws {
        let model = await modelWithProjects(#"[{"name":"a","path":"/p","is_git":true}]"#)
        XCTAssertNil(model.connectRequest(), "a sign-in needs a folder to run in")

        // Options of the agent session the user did not launch.
        model.selectStoredProject("a")
        model.opencodeAutoApprove = true
        model.worktree = true
        model.branchName = "feat"
        let inProject = try XCTUnwrap(model.connectRequest())

        XCTAssertEqual(inProject.project_name, "a")
        XCTAssertNil(inProject.cwd)
        XCTAssertEqual(inProject.cli, "opencode")
        XCTAssertEqual(inProject.ui, "terminal", "`opencode auth login` is a terminal program")
        XCTAssertEqual(inProject.opencode_options, OpenCodeOptionsModel(connect: true))
        XCTAssertEqual(inProject.worktree, false)
        XCTAssertNil(inProject.branch_name)

        model.selection = .customPath("/tmp/scratch")
        let inFolder = try XCTUnwrap(model.connectRequest())

        XCTAssertNil(inFolder.project_name)
        XCTAssertEqual(inFolder.cwd, "/tmp/scratch")
        XCTAssertEqual(inFolder.opencode_options, OpenCodeOptionsModel(connect: true))
    }

    func testConnectingAProviderSendsTheSignInAndRemembersNoOpenCodeChoices() async throws {
        StubURLProtocol.reset()
        let prefs = isolatedPrefs()
        let model = stubbedModel(prefs: prefs)
        model.selection = .stored("a")
        model.mode = .opencode
        model.opencodeAutoApprove = true
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-connect","state":"starting","created_at":"t"}"#
        )

        await model.connectOpenCodeProvider()

        XCTAssertNil(model.error)
        let body = try XCTUnwrap(try createBodies().last)
        XCTAssertEqual(body["cli"] as? String, "opencode")
        XCTAssertEqual(body["ui"] as? String, "terminal")
        let options = try XCTUnwrap(body["opencode_options"] as? [String: Any])
        XCTAssertEqual(options["connect"] as? Bool, true)
        XCTAssertEqual(options["auto_approve"] as? Bool, false)
        XCTAssertNil(prefs.getLastUsedOpenCodeOptions(), "a sign-in is not a launch of the agent")
        XCTAssertEqual(prefs.getLastUsedSessionId(), "s-connect")
    }

    func testAnAgentThatListsItsCLIsWithoutOpenCodeDisablesTheCard() async {
        // That agent predates the kind and would open a plain shell for it.
        let model = await modelWithCapabilities(
            #"{"cli":{"claude":{"available":true},"codex":{"available":true}}}"#
        )

        XCTAssertTrue(model.opencodeNeedsAgentUpdate)
        XCTAssertFalse(model.opencodeEnabled)
        XCTAssertFalse(model.isEnabled(.opencode))
        XCTAssertTrue(model.codexEnabled, "the agent's own CLIs stay enabled")
    }

    func testOpenCodeCardFollowsTheAgentsAvailabilityFlag() async {
        let missing = await modelWithCapabilities(#"{"cli":{"opencode":{"available":false}}}"#)
        XCTAssertFalse(missing.opencodeNeedsAgentUpdate, "known but not installed is not an outdated agent")
        XCTAssertFalse(missing.isEnabled(.opencode))

        let installed = await modelWithCapabilities(#"{"cli":{"opencode":{"available":true}}}"#)
        XCTAssertTrue(installed.isEnabled(.opencode))

        // No CLI list at all is an agent that reports nothing: unknown, not absent.
        let silent = await modelWithCapabilities("{}")
        XCTAssertFalse(silent.opencodeNeedsAgentUpdate)
        XCTAssertTrue(silent.isEnabled(.opencode))
    }

    func testOpenCodeWithoutChatOnThePCOpensTheTerminal() async throws {
        let model = await modelWithChat(#"{"claude":{"resume":true}}"#)
        model.selection = .stored("a")
        model.mode = .opencode

        let request = try XCTUnwrap(model.buildRequest(mode: .opencode))

        XCTAssertEqual(request.ui, "terminal")
        XCTAssertNotNil(model.opencodeChatUnavailableReason)
        XCTAssertFalse(model.canContinueSession)
    }

    func testOpenCodeContinueRequestUsesTheOpenCodeOptionsWithoutAWorktree() async {
        let model = await modelWithProjects(#"[{"name":"a","path":"/p","is_git":true}]"#)
        model.selectStoredProject("a")
        model.mode = .opencode
        model.opencodeAutoApprove = true
        model.worktree = true
        model.branchName = "feat"
        let stored = ChatStoredSession(
            session_id: "external-2222", cwd: "/srv/other", title: nil, updated_at: nil, open_session_id: nil
        )

        let request = model.continueRequest(for: stored)

        XCTAssertEqual(request.cli, "opencode")
        XCTAssertEqual(request.ui, "chat")
        XCTAssertEqual(request.chat_resume_id, "external-2222")
        XCTAssertEqual(request.cwd, "/srv/other")
        XCTAssertNil(request.project_name)
        XCTAssertNil(request.claude_options)
        XCTAssertNil(request.codex_options)
        XCTAssertEqual(request.opencode_options?.auto_approve, true)
        XCTAssertEqual(request.opencode_options?.worktree, false)
        XCTAssertNil(request.opencode_options?.branch_name)
    }

    // MARK: - GitHub Copilot

    func testCopilotRequestCarriesOnlyItsOwnOptionsBlock() async throws {
        let model = await modelWithProjects(#"[{"name":"a","path":"/p","is_git":true}]"#)
        model.selectStoredProject("a")
        model.copilotAllowAll = true
        model.worktree = true
        model.branchName = "feat"

        let request = try XCTUnwrap(model.buildRequest(mode: .copilot))

        XCTAssertEqual(request.cli, "copilot")
        XCTAssertEqual(request.project_name, "a")
        XCTAssertEqual(request.ui, "chat")
        XCTAssertNil(request.claude_options)
        XCTAssertNil(request.codex_options)
        XCTAssertNil(request.antigravity_options)
        XCTAssertEqual(request.copilot_options?.allow_all, true)
        XCTAssertEqual(request.copilot_options?.worktree, true, "the worktree rides inside copilot_options")
        XCTAssertEqual(request.copilot_options?.branch_name, "feat")
        XCTAssertEqual(request.worktree, false, "a managed CLI never uses the top-level worktree field")
        XCTAssertNil(request.branch_name)
    }

    func testCopilotCustomPathSendsTheFolderAndNeverAWorktree() throws {
        let model = makeModel()
        model.setCustomPath("/abs/path")
        model.worktree = true
        model.branchName = "feat"

        let request = try XCTUnwrap(model.buildRequest(mode: .copilot))

        XCTAssertEqual(request.cli, "copilot")
        XCTAssertEqual(request.cwd, "/abs/path")
        XCTAssertNil(request.project_name)
        XCTAssertEqual(request.copilot_options?.allow_all, false, "allow-all stays off until the user turns it on")
        XCTAssertEqual(request.copilot_options?.worktree, false)
        XCTAssertNil(request.copilot_options?.branch_name)
    }

    func testCreateSendsTheCopilotWireKeysAndRemembersTheChoices() async throws {
        StubURLProtocol.reset()
        let prefs = isolatedPrefs()
        let model = stubbedModel(prefs: prefs)
        model.selection = .stored("a")
        model.mode = .copilot
        model.copilotAllowAll = true
        model.copilotTerminalUI = true
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-new","state":"starting","created_at":"t"}"#
        )

        await model.create()

        XCTAssertNil(model.error)
        let body = try XCTUnwrap(try createBodies().last)
        XCTAssertEqual(body["cli"] as? String, "copilot")
        XCTAssertEqual(body["ui"] as? String, "terminal")
        let options = try XCTUnwrap(body["copilot_options"] as? [String: Any])
        XCTAssertEqual(options["allow_all"] as? Bool, true)
        XCTAssertEqual(options["worktree"] as? Bool, false)
        XCTAssertNil(body["claude_options"])
        XCTAssertNil(body["antigravity_options"])
        XCTAssertEqual(prefs.getLastUsedCopilotOptions(), CopilotLaunchPrefs(allowAll: true, terminalUI: true))
        XCTAssertNil(prefs.getLastUsedAntigravityOptions(), "a Copilot launch writes no other agent's choices")

        let next = stubbedModel(prefs: prefs)
        XCTAssertTrue(next.copilotAllowAll, "the next wizard starts from the remembered choices")
        XCTAssertTrue(next.copilotTerminalUI)
        XCTAssertFalse(next.antigravityTerminalUI)
    }

    func testCopilotCardIsOffForAnAgentFromBeforeCopilotSupport() async {
        // That agent lists its CLIs without Copilot, and would open a plain
        // shell for `cli = "copilot"`.
        let older = await modelWithCapabilities(
            #"{"cli":{"claude":{"available":true},"codex":{"available":true}}}"#
        )
        XCTAssertFalse(older.isEnabled(.copilot))
        XCTAssertTrue(older.isEnabled(.codex), "the agent's other CLIs stay usable")
        older.selectMode(.copilot)
        XCTAssertNil(older.mode, "a disabled card must not open the Copilot config step")

        let notInstalled = await modelWithCapabilities(#"{"cli":{"copilot":{"available":false}}}"#)
        XCTAssertFalse(notInstalled.isEnabled(.copilot))

        let noCliList = await modelWithCapabilities("{}")
        XCTAssertTrue(noCliList.isEnabled(.copilot), "an agent that reports no CLI list reads as unknown")

        let installed = await modelWithCapabilities(#"{"cli":{"copilot":{"available":true}}}"#)
        XCTAssertTrue(installed.isEnabled(.copilot))
        installed.selectMode(.copilot)
        XCTAssertEqual(installed.mode, .copilot)
    }

    func testCopilotWithoutChatOnThePCOpensTheTerminalAndCannotContinue() async throws {
        let model = await modelWithCapabilities(
            #"{"cli":{"copilot":{"available":true}},"chat":{"claude":{"resume":true}}}"#
        )
        model.selection = .stored("a")
        model.mode = .copilot

        let request = try XCTUnwrap(model.buildRequest(mode: .copilot))

        XCTAssertEqual(request.ui, "terminal")
        XCTAssertNotNil(model.copilotChatUnavailableReason)
        XCTAssertFalse(model.canContinueSession)
    }

    func testCopilotContinueIsOfferedOnlyForTheChatView() async {
        let model = await modelWithCapabilities(
            #"{"cli":{"copilot":{"available":true}},"chat":{"copilot":{"resume":true,"live":false}}}"#
        )
        model.selection = .stored("a")
        model.mode = .copilot

        XCTAssertTrue(model.canContinueSession)
        XCTAssertNil(model.copilotChatUnavailableReason)

        model.copilotTerminalUI = true

        XCTAssertFalse(model.canContinueSession)
    }

    func testCopilotContinueRequestUsesTheCopilotOptionsWithoutAWorktree() {
        let model = makeModel()
        model.mode = .copilot
        model.selection = .stored("a")
        model.copilotAllowAll = true
        model.worktree = true
        model.branchName = "feat"
        let stored = ChatStoredSession(
            session_id: "external-3333", cwd: "/srv/other", title: nil, updated_at: nil, open_session_id: nil
        )

        let request = model.continueRequest(for: stored)

        XCTAssertEqual(request.cli, "copilot")
        XCTAssertEqual(request.ui, "chat")
        XCTAssertEqual(request.chat_resume_id, "external-3333")
        XCTAssertEqual(request.cwd, "/srv/other")
        XCTAssertNil(request.project_name)
        XCTAssertNil(request.claude_options)
        XCTAssertNil(request.codex_options)
        XCTAssertNil(request.antigravity_options)
        XCTAssertEqual(request.copilot_options?.allow_all, true)
        XCTAssertEqual(request.copilot_options?.worktree, false)
        XCTAssertNil(request.copilot_options?.branch_name)
    }

    // MARK: - Codex continue after the Codex sign-in

    private let externalSession = ChatStoredSession(
        session_id: "external-2222", cwd: "/srv/other", title: nil, updated_at: nil, open_session_id: nil
    )

    /// A Codex "Continue a session" whose create asks for the Codex sign-in.
    private func codexContinueNeedingSignIn() async -> CreateSessionModel {
        StubURLProtocol.reset()
        let model = stubbedModel()
        model.selection = .stored("a")
        model.mode = .codex
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"codex_auth_required":true}}"#)
        await model.continueSession(externalSession)
        XCTAssertTrue(model.showCodexLogin)
        return model
    }

    private func createBodies() throws -> [[String: Any]] {
        try zip(StubURLProtocol.requests, StubURLProtocol.bodies)
            .filter { $0.0.url?.path == "/api/sessions" && $0.0.httpMethod == "POST" }
            .map { try XCTUnwrap(JSONSerialization.jsonObject(with: $0.1) as? [String: Any]) }
    }

    func testSigningInRetriesTheSameCodexContinue() async throws {
        let model = await codexContinueNeedingSignIn()
        StubURLProtocol.enqueue(status: 200, body: #"{"authenticated":true}"#)
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-resumed","state":"starting","created_at":"t"}"#
        )
        model.codexApiKey = "sk-user-key"

        await model.submitCodexLogin()

        XCTAssertNil(model.error)
        let bodies = try createBodies()
        XCTAssertEqual(bodies.count, 2)
        XCTAssertEqual(bodies.last?["chat_resume_id"] as? String, "external-2222")
        XCTAssertEqual(bodies.last?["cwd"] as? String, "/srv/other")
        XCTAssertEqual(bodies.last?["cli"] as? String, "codex")
    }

    func testCancellingTheCodexSignInForgetsTheContinue() async throws {
        let model = await codexContinueNeedingSignIn()
        model.cancelCodexLogin()
        StubURLProtocol.enqueue(status: 200, body: #"{"authenticated":true}"#)
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"session_id":"s-new","state":"starting","created_at":"t"}"#
        )
        model.codexApiKey = "sk-user-key"

        await model.submitCodexLogin()

        let bodies = try createBodies()
        XCTAssertEqual(bodies.count, 2)
        XCTAssertNil(bodies.last?["chat_resume_id"] as? String, "a later sign-in starts a new session")
        XCTAssertEqual(bodies.last?["project_name"] as? String, "a")
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

    func testCustomPathDropsThePickedSuggestionsTrailingSeparatorButNotARoot() throws {
        // A picked folder suggestion leaves `/srv/app/` in the field; the cwd
        // and the stored recent must be `/srv/app`, or recents list both.
        let model = makeModel()
        model.setCustomPath(" /srv/app/ ")
        XCTAssertEqual(try XCTUnwrap(model.buildRequest(mode: .standard)).cwd, "/srv/app")

        model.setCustomPath("/")
        XCTAssertEqual(try XCTUnwrap(model.buildRequest(mode: .standard)).cwd, "/")
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

    // MARK: - Custom agent types: save flow

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
}
