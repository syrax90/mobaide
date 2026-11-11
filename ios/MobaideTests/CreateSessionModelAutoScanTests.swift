import XCTest
@testable import Mobaide

/// Auto Scan half of the create-session wizard: the launch gate, the mode
/// card's reasons, prompt seeding, the options block that reaches
/// `/api/autoscan/start`, its own prefs bucket, and the Projects shortcut.
@MainActor
final class CreateSessionModelAutoScanTests: XCTestCase {
    /// `linear` is launchable, `plain` has no Linear key, `seeded` carries its
    /// own Auto Scan prompt.
    private let projectsJSON = """
    [{"name":"linear","path":"/l","has_linear_key":true,"is_git":true},
     {"name":"plain","path":"/p","has_linear_key":false,"is_git":true},
     {"name":"seeded","path":"/s","has_linear_key":true,"is_git":true,"auto_scan_prompt":"per-project prompt"}]
    """

    private func idleWsClient() -> AutoScanWsClient {
        AutoScanWsClient(
            webSocketFactory: .live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            urlProvider: { nil },
            isAuthenticated: { false }
        )
    }

    private func stubbedAgentAPI() -> AgentAPI {
        AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        )
    }

    private func makeBus(_ harness: SessionHarness) -> AgentEventBus {
        AgentEventBus(
            webSocketFactory: .live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
    }

    private func makeAutoScanRepo() -> AutoScanRepository {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        return AutoScanRepository(
            agentAPI: stubbedAgentAPI(),
            agentEventBus: makeBus(harness),
            wsClient: idleWsClient()
        )
    }

    private func makeModel(
        autoScanRepo: AutoScanRepository,
        prefs: PreferencesStore? = nil,
        autoScanProject: String? = nil
    ) -> CreateSessionModel {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = makeBus(harness)
        let agentAPI = stubbedAgentAPI()
        return CreateSessionModel(
            agentAPI: agentAPI,
            sessionRepository: SessionRepository(
                agentAPI: agentAPI,
                agentEventBus: bus,
                terminalSessionStore: TerminalTestSupport.store(harness)
            ),
            preferencesStore: prefs ?? isolatedPrefs(),
            tokenStore: harness.tokenStore,
            customAgentTypesRepository: CustomAgentTypesRepository(
                centralAPI: TerminalTestSupport.stubbedCentralAPI(),
                userIdentityStore: harness.userIdentityStore
            ),
            autoScanRepository: autoScanRepo,
            autoScanProject: autoScanProject
        )
    }

    private func isolatedPrefs() -> PreferencesStore {
        PreferencesStore(defaults: UserDefaults(suiteName: "cw-autoscan-\(UUID().uuidString)")!)
    }

    private func listJSON(scans: [String], prompt: String?, maxScans: Int) -> String {
        let items = scans
            .map { #"{"project_name":"\#($0)","status":"running","started_at":1.5,"last_activity_at":1.5}"# }
            .joined(separator: ",")
        let promptField = prompt.map { #","auto_scan_prompt":"\#($0)""# } ?? ""
        return #"{"scans":[\#(items)],"max_scans":\#(maxScans)\#(promptField)}"#
    }

    private func seedList(
        _ repo: AutoScanRepository,
        scans: [String] = [],
        prompt: String? = nil,
        maxScans: Int = 5
    ) async {
        StubURLProtocol.enqueue(status: 200, body: listJSON(scans: scans, prompt: prompt, maxScans: maxScans))
        await repo.refresh()
    }

    /// The wizard snapshots `currentScans` in `init`, so seed the repository
    /// before building the model. On the preset path `load()` also re-reads the
    /// scan list, so that request gets a response mirroring the seeded state
    /// (`max_scans` reverts to the default — no preset test pins it).
    private func loadedModel(
        autoScanRepo: AutoScanRepository,
        prefs: PreferencesStore? = nil,
        autoScanProject: String? = nil
    ) async -> CreateSessionModel {
        StubURLProtocol.enqueue(status: 200, body: projectsJSON)
        if autoScanProject != nil {
            StubURLProtocol.enqueue(
                status: 200,
                body: listJSON(
                    scans: autoScanRepo.currentScans.map(\.project_name),
                    prompt: autoScanRepo.autoScanPrompt,
                    maxScans: 5
                )
            )
        }
        let model = makeModel(autoScanRepo: autoScanRepo, prefs: prefs, autoScanProject: autoScanProject)
        await model.load()
        return model
    }

    private func versionJSON(claudeAvailable: Bool) -> String {
        #"""
        {"name":"agent","server_version":"1.0.0","protocol_versions":[7],"min_client_version":1,
         "capabilities":{"cli":{"claude":{"available":\#(claudeAvailable)}}}}
        """#
    }

    // MARK: - Launch gate

    func testAutoScanGateNeedsALinearKeyedStoredProject() async {
        StubURLProtocol.reset()
        let model = await loadedModel(autoScanRepo: makeAutoScanRepo())

        XCTAssertFalse(model.autoScanEnabled, "nothing is selected yet")

        model.selectStoredProject("plain")
        XCTAssertFalse(model.autoScanEnabled)
        XCTAssertFalse(model.isEnabled(.autoScan))
        XCTAssertEqual(model.reason(for: .autoScan), "Auto Scan needs a Linear API key for this project.")

        model.setCustomPath("/abs/path")
        XCTAssertFalse(model.autoScanEnabled, "a custom path has no stored project to carry a Linear key")

        model.selectStoredProject("linear")
        XCTAssertTrue(model.autoScanEnabled)
        XCTAssertTrue(model.isEnabled(.autoScan))
        XCTAssertEqual(model.reason(for: .autoScan), "Scan Linear for matching issues and launch sessions.")
    }

    func testAutoScanGateReportsAMissingClaudeCliAheadOfTheLinearKey() async {
        StubURLProtocol.reset()
        let model = await loadedModel(autoScanRepo: makeAutoScanRepo())
        model.selectStoredProject("plain")
        StubURLProtocol.enqueue(status: 200, body: versionJSON(claudeAvailable: false))

        await model.refreshCliAvailability()

        XCTAssertFalse(model.claudeEnabled)
        XCTAssertFalse(model.isEnabled(.autoScan))
        XCTAssertEqual(
            model.reason(for: .autoScan),
            "Auto Scan needs the Claude CLI, which isn't installed on the agent.",
            "the CLI gate outranks the missing Linear key"
        )
    }

    func testRunningScanKeepsTheCardTappableAndOutranksEveryOtherReason() async {
        StubURLProtocol.reset()
        let repo = makeAutoScanRepo()
        await seedList(repo, scans: ["plain"])
        let model = await loadedModel(autoScanRepo: repo)
        model.selectStoredProject("plain")
        StubURLProtocol.enqueue(status: 200, body: versionJSON(claudeAvailable: false))
        await model.refreshCliAvailability()

        XCTAssertFalse(model.autoScanEnabled, "the launch gate still refuses")
        XCTAssertTrue(model.isEnabled(.autoScan), "an existing scan stays openable")
        XCTAssertEqual(model.reason(for: .autoScan), "Already running for plain — tap to open the live monitor.")
    }

    func testObserveScansPicksUpAScanStartedElsewhere() async {
        StubURLProtocol.reset()
        let repo = makeAutoScanRepo()
        let model = await loadedModel(autoScanRepo: repo)
        model.selectStoredProject("linear")
        XCTAssertNil(model.runningScan)

        StubURLProtocol.enqueue(status: 200, body: listJSON(scans: ["linear"], prompt: nil, maxScans: 5))
        let observe = Task { await model.observeScans() }
        defer { observe.cancel() }

        await waitUntil { model.runningScan != nil }
        XCTAssertEqual(model.runningScan?.project_name, "linear")
    }

    // MARK: - Mode selection and prompt seeding

    func testSelectingAutoScanWithARunningScanOpensTheMonitorInsteadOfConfig() async throws {
        StubURLProtocol.reset()
        let repo = makeAutoScanRepo()
        await seedList(repo, scans: ["linear"])
        let model = await loadedModel(autoScanRepo: repo)
        model.selectStoredProject("linear")
        model.continueFromProject()
        let nav = StreamCollector(model.autoScanNavEvents())

        model.selectMode(.autoScan)

        let opened = try await nav.next()
        XCTAssertEqual(opened, "linear")
        XCTAssertEqual(model.step, .modePick, "the wizard must not enter a config step that would only 409")
        XCTAssertNil(model.mode)
    }

    func testAutoScanPromptPrefersTheProjectPromptThenTheAgentDefault() async {
        StubURLProtocol.reset()
        let repo = makeAutoScanRepo()
        await seedList(repo, prompt: "agent-wide prompt")
        let model = await loadedModel(autoScanRepo: repo)

        model.selectStoredProject("seeded")
        model.selectMode(.autoScan)
        XCTAssertEqual(model.step, .config)
        XCTAssertEqual(model.autoScanInitialPrompt, "per-project prompt")

        model.back()
        model.selectStoredProject("linear")
        model.selectMode(.autoScan)
        XCTAssertEqual(
            model.autoScanInitialPrompt, "agent-wide prompt",
            "a project without its own prompt re-seeds from the agent-wide default"
        )
    }

    /// The agent-wide default rides in on `observeScans()`'s refresh, which
    /// races the user. Reaching the step first must not latch the project as
    /// seeded with an empty prompt: nothing would re-seed it, and the wizard
    /// would hide the prompt the scan actually runs with for its whole life.
    func testAutoScanPromptPicksUpAnAgentDefaultThatLandsAfterTheStep() async {
        StubURLProtocol.reset()
        let repo = makeAutoScanRepo()
        XCTAssertNil(repo.autoScanPrompt, "a cold repository is the whole point — do not seed this one")
        let model = await loadedModel(autoScanRepo: repo)
        model.selectStoredProject("linear")

        model.selectMode(.autoScan)
        XCTAssertEqual(model.step, .config)
        XCTAssertEqual(model.autoScanInitialPrompt, "", "no project prompt, and the agent default has not arrived")

        StubURLProtocol.enqueue(status: 200, body: listJSON(scans: [], prompt: "agent-wide prompt", maxScans: 5))
        let observe = Task { await model.observeScans() }
        defer { observe.cancel() }

        await waitUntil { !model.autoScanInitialPrompt.isEmpty }
        XCTAssertEqual(
            model.autoScanInitialPrompt, "agent-wide prompt",
            "the refresh that resolves the default has to re-seed the step the user already reached"
        )
    }

    func testALateAgentDefaultLeavesAnInFlightPromptEditAlone() async {
        StubURLProtocol.reset()
        let model = await loadedModel(autoScanRepo: makeAutoScanRepo())
        model.selectStoredProject("linear")
        model.selectMode(.autoScan)
        model.autoScanInitialPrompt = "hand-written"

        StubURLProtocol.enqueue(status: 200, body: listJSON(scans: ["linear"], prompt: "agent-wide prompt", maxScans: 5))
        let observe = Task { await model.observeScans() }
        defer { observe.cancel() }

        // `activeScans` is assigned in the same synchronous loop body as the
        // re-seed attempt, with no suspension between them, so observing it
        // pins the assertion past the one moment the typed prompt could have
        // been overwritten.
        await waitUntil { model.runningScan != nil }
        XCTAssertEqual(model.autoScanInitialPrompt, "hand-written")
    }

    // MARK: - Options block

    func testAutoScanOptionsForceWorktreeAndDefaultToSkippingPermissions() {
        let model = makeModel(autoScanRepo: makeAutoScanRepo())

        let options = model.autoScanClaudeOptions()

        XCTAssertTrue(options.worktree, "each matching issue runs in its own worktree — never the user's choice")
        XCTAssertTrue(options.skip_permissions, "an unattended scan would strand itself on a permission prompt")
        XCTAssertFalse(options.plan_mode)
        XCTAssertNil(options.initial_prompt)
        XCTAssertNil(options.append_system_prompt)
        XCTAssertNil(options.model)
        XCTAssertEqual(options.effort, "high")
    }

    func testAutoScanOptionsCarryTogglesAndDropBlankPrompts() {
        let model = makeModel(autoScanRepo: makeAutoScanRepo())
        model.autoScanSkipPermissions = false
        model.autoScanPlanMode = true
        model.autoScanNoMemory = true
        model.autoScanInitialPrompt = "  \n "
        model.autoScanClaudeAppendSystemPrompt = " "

        let options = model.autoScanClaudeOptions()

        XCTAssertFalse(options.skip_permissions)
        XCTAssertTrue(options.plan_mode)
        XCTAssertTrue(options.no_memory)
        XCTAssertNil(options.initial_prompt, "a whitespace-only prompt is omitted, not sent blank")
        XCTAssertNil(options.append_system_prompt)
        XCTAssertTrue(options.worktree)
    }

    func testAutoScanFastModeAndUltracodeOverrideModelAndEffort() {
        let model = makeModel(autoScanRepo: makeAutoScanRepo())
        model.autoScanClaudeModel = "haiku"
        model.autoScanClaudeEffort = "max"
        XCTAssertEqual(model.autoScanClaudeOptions().effort, "high", "haiku cannot serve max")

        model.autoScanClaudeFastMode = true
        model.autoScanClaudeUltracode = true
        let options = model.autoScanClaudeOptions()

        XCTAssertEqual(options.model, "opus")
        XCTAssertEqual(options.effort, "xhigh")
        XCTAssertTrue(options.fast_mode)
        XCTAssertTrue(options.ultracode)
    }

    func testAutoScanDebugIsReadFromItsOwnBucketNotTheClaudeStep() {
        let prefs = isolatedPrefs()
        prefs.setLastUsedClaudeOptions(ClaudeLaunchPrefs(debug: false))
        prefs.setLastUsedAutoScanOptions(ClaudeLaunchPrefs(debug: true))

        let model = makeModel(autoScanRepo: makeAutoScanRepo(), prefs: prefs)

        XCTAssertTrue(model.autoScanClaudeDebug)
        XCTAssertFalse(model.claudeDebug, "the Claude Code step keeps its own saved OFF choice")
        XCTAssertTrue(model.autoScanClaudeOptions().debug)

        model.autoScanClaudeDebug = false
        model.claudeDebug = true
        XCTAssertFalse(
            model.autoScanClaudeOptions().debug,
            "a Claude Code edit must not bleed into the scan's options"
        )
    }

    func testStartAutoScanSendsDebugAndPersistsItIntoItsOwnBucket() async throws {
        StubURLProtocol.reset()
        let prefs = isolatedPrefs()
        let model = await loadedModel(autoScanRepo: makeAutoScanRepo(), prefs: prefs)
        model.selectStoredProject("linear")
        model.autoScanClaudeDebug = true
        StubURLProtocol.enqueue(status: 200, body: "{}")

        await model.startAutoScan()

        XCTAssertNil(model.error)
        let data = try XCTUnwrap(StubURLProtocol.bodies.last)
        let body = try WireJSON.decoder().decode(JSONValue.self, from: data)
        XCTAssertEqual(body["claude_options"]?["debug"]?.boolValue, true)
        XCTAssertEqual(prefs.getLastUsedAutoScanOptions()?.debug, true)
        XCTAssertNil(
            prefs.getLastUsedClaudeOptions(),
            "an Auto Scan launch leaves the Claude Code bucket alone"
        )
    }

    // MARK: - Submit

    func testStartAutoScanPostsTheOptionsPersistsItsOwnBucketAndOpensTheMonitor() async throws {
        StubURLProtocol.reset()
        let prefs = isolatedPrefs()
        prefs.setLastUsedClaudeOptions(ClaudeLaunchPrefs(model: "sonnet", effort: "low"))
        let model = await loadedModel(autoScanRepo: makeAutoScanRepo(), prefs: prefs)
        model.selectStoredProject("linear")
        model.autoScanClaudeModel = "opus"
        model.autoScanPlanMode = true
        let nav = StreamCollector(model.autoScanNavEvents())
        StubURLProtocol.enqueue(status: 200, body: "{}")

        await model.startAutoScan()

        XCTAssertNil(model.error)
        XCTAssertFalse(model.autoScanStarting)
        let opened = try await nav.next()
        XCTAssertEqual(opened, "linear")

        let sent = try XCTUnwrap(StubURLProtocol.requests.last)
        XCTAssertEqual(sent.httpMethod, "POST")
        XCTAssertEqual(sent.url?.path, "/api/autoscan/start")
        let data = try XCTUnwrap(StubURLProtocol.bodies.last)
        let body = try WireJSON.decoder().decode(JSONValue.self, from: data)
        XCTAssertEqual(body["project_name"]?.stringValue, "linear")
        XCTAssertEqual(body["claude_options"]?["worktree"]?.boolValue, true)
        XCTAssertEqual(body["claude_options"]?["plan_mode"]?.boolValue, true)
        XCTAssertEqual(body["claude_options"]?["skip_permissions"]?.boolValue, true)
        XCTAssertEqual(body["claude_options"]?["model"]?.stringValue, "opus")

        XCTAssertEqual(prefs.getLastUsedProjectName(), "linear")
        let savedAutoScan = prefs.getLastUsedAutoScanOptions()
        XCTAssertEqual(savedAutoScan?.model, "opus")
        XCTAssertEqual(savedAutoScan?.planMode, true)
        XCTAssertEqual(savedAutoScan?.skipPermissions, true)
        let savedClaude = prefs.getLastUsedClaudeOptions()
        XCTAssertEqual(savedClaude?.model, "sonnet", "an Auto Scan launch leaves the Claude Code bucket alone")
        XCTAssertEqual(savedClaude?.planMode, false)
    }

    func testStartAutoScanConflictOpensTheMonitorRatherThanErroring() async throws {
        StubURLProtocol.reset()
        let model = await loadedModel(autoScanRepo: makeAutoScanRepo())
        model.selectStoredProject("linear")
        let nav = StreamCollector(model.autoScanNavEvents())
        StubURLProtocol.enqueue(status: 409, body: "{}")

        await model.startAutoScan()

        let opened = try await nav.next()
        XCTAssertEqual(opened, "linear", "a scan started elsewhere mid-wizard resolves to its monitor")
        XCTAssertNil(model.error)
        XCTAssertFalse(model.autoScanStarting)
    }

    // MARK: - Prefs prefill

    func testPrefillReadsTheAutoScanBucketWithoutTouchingTheClaudeFields() {
        let prefs = isolatedPrefs()
        prefs.setLastUsedClaudeOptions(ClaudeLaunchPrefs(model: "sonnet", effort: "low"))
        prefs.setLastUsedAutoScanOptions(ClaudeLaunchPrefs(
            model: "haiku", effort: "medium", appendSystemPrompt: "scan sys",
            skipPermissions: false, noMemory: true, planMode: true
        ))

        let model = makeModel(autoScanRepo: makeAutoScanRepo(), prefs: prefs)

        XCTAssertEqual(model.autoScanClaudeModel, "haiku")
        XCTAssertEqual(model.autoScanClaudeEffort, "medium")
        XCTAssertEqual(model.autoScanClaudeAppendSystemPrompt, "scan sys")
        XCTAssertFalse(model.autoScanSkipPermissions, "a persisted off-toggle overrides the ON default")
        XCTAssertTrue(model.autoScanNoMemory)
        XCTAssertTrue(model.autoScanPlanMode)

        XCTAssertEqual(model.claudeModel, "sonnet", "the Claude Code step keeps its own last-used options")
        XCTAssertEqual(model.claudeEffort, "low")
    }

    // MARK: - Projects-screen shortcut

    func testProjectsShortcutPresetOpensTheAutoScanConfigStep() async {
        StubURLProtocol.reset()
        let repo = makeAutoScanRepo()
        await seedList(repo, prompt: "agent-wide prompt")
        let model = await loadedModel(autoScanRepo: repo, autoScanProject: "seeded")

        XCTAssertEqual(model.selection, .stored("seeded"))
        XCTAssertEqual(model.mode, .autoScan)
        XCTAssertEqual(model.step, .config)
        XCTAssertEqual(model.autoScanInitialPrompt, "per-project prompt")
    }

    /// The preset applies exactly once, off `runningScan`, so a repository
    /// nobody warmed — no `refresh()`, no bus frame, empty at `init` — has to be
    /// filled inside `load()`. Otherwise the shortcut drops an already-scanning
    /// project into the config step, whose only exit is a start that 409s — and
    /// not even that when the Claude CLI is missing and Start is disabled.
    func testProjectsShortcutPresetResolvesARunningScanFromAColdRepository() async throws {
        StubURLProtocol.reset()
        let repo = makeAutoScanRepo()
        XCTAssertTrue(repo.currentScans.isEmpty, "cold cache is the whole point — do not seed this repository")
        StubURLProtocol.enqueue(status: 200, body: projectsJSON)
        StubURLProtocol.enqueue(status: 200, body: listJSON(scans: ["linear"], prompt: nil, maxScans: 5))
        let model = makeModel(autoScanRepo: repo, autoScanProject: "linear")

        await model.load()

        XCTAssertEqual(model.selection, .stored("linear"))
        XCTAssertNotEqual(model.step, .config, "a running scan routes to the monitor, never to a config step")
        XCTAssertNil(model.mode)
        let nav = StreamCollector(model.autoScanNavEvents())
        let opened = try await nav.next()
        XCTAssertEqual(opened, "linear")
    }
}
