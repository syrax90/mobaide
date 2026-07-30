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

    /// Repository over `StubURLProtocol` with its own logged-in identity —
    /// pass the same instance to the model when a test seeds or inspects the
    /// custom-type cache.
    private func makeTypesRepo() -> CustomAgentTypesRepository {
        CustomAgentTypesRepository(
            centralAPI: TerminalTestSupport.stubbedCentralAPI(),
            userIdentityStore: SessionHarness(userLoggedIn: true).userIdentityStore
        )
    }

    private func makeModel(
        prefs: PreferencesStore? = nil,
        typesRepo: CustomAgentTypesRepository? = nil
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
            customAgentTypesRepository: typesRepo ?? makeTypesRepo()
        )
    }

    private func stubbedModel(typesRepo: CustomAgentTypesRepository? = nil) -> CreateSessionModel {
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
            preferencesStore: isolatedPrefs(),
            customAgentTypesRepository: typesRepo ?? makeTypesRepo()
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
        XCTAssertEqual(request.claude_options?.skip_permissions, false)
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

    func testCustomTypeCarriesCliAndTopLevelWorktree() async {
        // Custom agent types launch with plain-terminal semantics plus the
        // `cli` discriminator — worktree/branch ride the top level exactly
        // like `.shell` (compare testShellCarriesWorktreeOnTopLevel).
        let model = await modelWithProjects(#"[{"name":"a","path":"/p","is_git":true}]"#)
        model.selectStoredProject("a")
        model.worktree = true
        model.branchName = "feat"
        let request = model.buildRequest(mode: .custom("ab12cd34ef56"))
        XCTAssertEqual(request.cli, "custom_ab12cd34ef56")
        XCTAssertNil(request.command, "the stored command is resolved relay-side — the client never sends it")
        XCTAssertNil(request.claude_options)
        XCTAssertNil(request.codex_options)
        XCTAssertNil(request.antigravity_options)
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
            _ = released.wait(timeout: .now() + 2)
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
            _ = released.wait(timeout: .now() + 2)
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
