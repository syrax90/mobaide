import XCTest
@testable import Mobaide

/// The Auto Scan step on an agent with `capabilities.autoscan`
/// (`AutoScanSetupModel`): the choices seed from the workspace's saved ones
/// with no default the user did not pick, Start waits for every choice, and
/// the start body carries only the chosen source's filter. Inputs are the
/// shared golden fixtures, so the agent and the other clients read the same
/// shapes.
@MainActor
final class AutoScanSetupModelTests: XCTestCase {
    private struct StubAgentTokens: AgentTokenProviding {
        func validToken() async -> AgentToken? { nil }
        func refreshedToken() async -> String? { nil }
    }

    private func makeModel() throws -> AutoScanSetupModel {
        let model = AutoScanSetupModel(
            agentAPI: AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
        )
        let version = try FixtureLoader.decodeCase(
            VersionInfoResponse.self,
            path: "dto/agent/VersionInfoResponse.json",
            name: "autoscan_capability"
        )
        model.applyProbe(version)
        return model
    }

    private func workspace(_ name: String) throws -> WorkspaceInfo {
        try FixtureLoader.decodeCase(WorkspaceInfo.self, path: "dto/agent/ProjectInfo.json", name: name)
    }

    private static let readyGitHubStatus =
        #"{"installed":true,"version":"2.83.0","signed_in":true,"account":"octocat","host":"github.com","repo":"acme/app","repo_url":"https://github.com/acme/app","problem":null,"can_install":false,"install_command":null}"#

    /// The saved GitHub choices, with the PC's `gh` checked and ready.
    private func checkedGitHubModel(labels: String = #"{"labels":[]}"#) async throws -> AutoScanSetupModel {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: Self.readyGitHubStatus)
        StubURLProtocol.enqueue(status: 200, body: labels)
        let model = AutoScanSetupModel(
            agentAPI: AgentAPI(
                baseURL: { "http://agent.example.com" },
                tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
                client: HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
            )
        )
        model.applyProbe(
            try FixtureLoader.decodeCase(
                VersionInfoResponse.self,
                path: "dto/agent/VersionInfoResponse.json",
                name: "autoscan_capability"
            )
        )
        model.prepare(for: try workspace("auto_scan_choices"))
        await model.refreshGitHubStatus()
        return model
    }

    func testTheCapabilityFixtureOffersEveryAgentButOnlyInstalledOnesCanRun() throws {
        let model = try makeModel()
        let older = try FixtureLoader.decodeCase(
            VersionInfoResponse.self,
            path: "dto/agent/VersionInfoResponse.json",
            name: "missing_capabilities_defaults_to_empty_object"
        )

        XCTAssertEqual(model.sources, [.github, .linear])
        XCTAssertEqual(
            model.agents,
            ["claude", "codex", "antigravity", "opencode", "copilot", "cursor", "junie", "pi"]
        )
        XCTAssertTrue(model.isAgentAvailable("codex"))
        XCTAssertFalse(model.isAgentAvailable("pi"))
        XCTAssertTrue(model.canRunGitHubSetup)
        XCTAssertNil(older.autoScanCapability)
    }

    func testSeedsTheSavedChoicesAndNothingWhenNothingWasChosen() throws {
        let model = try makeModel()
        model.prepare(for: try workspace("auto_scan_choices"))

        XCTAssertEqual(model.source, .github)
        XCTAssertEqual(model.cli, "codex")
        XCTAssertEqual(
            model.githubFilter,
            GitHubScanFilterDto(labels: ["ai-ready", "bot"], assignee: "@me", issue_types: ["Bug"])
        )
        XCTAssertEqual(model.codexOptions.model, "gpt-5.5")
        XCTAssertEqual(model.linearTriggerStates, ["Todo"])
        XCTAssertEqual(model.linearDoneState, "Shipped")

        let fresh = try makeModel()
        fresh.prepare(for: try workspace("auto_scan_nothing_chosen"))

        XCTAssertNil(fresh.source)
        XCTAssertNil(fresh.cli)
        XCTAssertEqual(fresh.githubFilter.labels, [])
        // Not the legacy defaults the workspace still carries for older clients.
        XCTAssertTrue(fresh.linearTriggerStates.isEmpty)
        XCTAssertEqual(fresh.linearActiveState, "")
    }

    func testPreparingTheSameWorkspaceAgainKeepsTheEdits() throws {
        let model = try makeModel()
        let saved = try workspace("auto_scan_choices")
        model.prepare(for: saved)
        model.source = .linear
        model.cli = "pi"

        model.prepare(for: saved)

        XCTAssertEqual(model.source, .linear)
        XCTAssertEqual(model.cli, "pi")
    }

    func testStartWaitsForEveryChoiceAndAnInstalledAgent() throws {
        let model = try makeModel()
        model.prepare(for: try workspace("auto_scan_choices"))
        model.source = .linear

        XCTAssertNil(model.startBlocker)

        model.cli = "pi"
        XCTAssertNotNil(model.startBlocker, "listed by the agent, but not installed on the PC")
        model.cli = "aider"
        XCTAssertNotNil(model.startBlocker, "a kind the agent does not offer")
        model.cli = nil
        XCTAssertNotNil(model.startBlocker)
        model.cli = "codex"
        model.linearDoneState = " "
        XCTAssertNotNil(model.startBlocker)
        model.linearDoneState = "Shipped"
        model.source = .github
        XCTAssertNotNil(model.startBlocker, "GitHub was not checked yet")
        model.source = nil
        XCTAssertNotNil(model.startBlocker)
    }

    /// Start stays disabled for any value the agent would answer with 422.
    func testGitHubStartWaitsForValuesTheAgentAccepts() async throws {
        let model = try await checkedGitHubModel()
        XCTAssertNil(model.startBlocker)

        model.githubAssignee = String(repeating: "a", count: 101)
        XCTAssertNotNil(model.startBlocker)
        model.githubAssignee = String(repeating: "a", count: 100)
        XCTAssertNil(model.startBlocker)

        // " Bot " is a trigger label: adding it would make the issue match again.
        for rejected in ["doing,now", String(repeating: "l", count: 101), " Bot "] {
            model.githubActiveLabel = rejected
            XCTAssertNotNil(model.startBlocker, rejected)
        }
        model.githubActiveLabel = " ai-doing "
        XCTAssertNil(model.startBlocker)

        model.addGitHubLabel("needs,triage")
        XCTAssertEqual(model.githubLabels, ["ai-ready", "bot"])
        XCTAssertNotNil(model.githubLabelError)

        XCTAssertNil(model.startBlocker(prompt: String(repeating: "p", count: 32_000)))
        XCTAssertNotNil(model.startBlocker(prompt: String(repeating: "p", count: 32_001)))
    }

    /// A label created on GitHub after the step opened can only be picked
    /// once Check again reads the list again.
    func testCheckAgainReadsTheRepositoryLabelsAgain() async throws {
        let model = try await checkedGitHubModel(labels: #"{"labels":[{"name":"bug","color":"d73a4a"}]}"#)
        StubURLProtocol.enqueue(status: 200, body: Self.readyGitHubStatus)
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"labels":[{"name":"bug","color":"d73a4a"},{"name":"auto-scan","color":"0e8a16"}]}"#
        )

        await model.refreshGitHubStatus(userAsked: true)

        XCTAssertEqual(model.githubRepoLabels.map(\.name), ["bug", "auto-scan"])
    }

    func testTheGitHubStartBodyEncodesToTheFixtureWire() throws {
        // The probe lists the chat view for Codex: the saved "chat" is sent as is.
        let model = try probedModel(codexChat: true)
        model.prepare(for: try workspace("auto_scan_choices"))
        model.githubActiveLabel = " ai-doing "
        model.toggleGitHubIssueType("Task")
        model.codexOptions.full_auto = false
        model.codexOptions.auto_review = true
        model.codexOptions.reasoning_effort = nil

        let request = try XCTUnwrap(
            model.startRequest(
                workspaceName: "p",
                claudeOptions: ClaudeOptionsModel(),
                initialPrompt: ""
            )
        )
        let encoded = try WireJSON.decoder().decode(JSONValue.self, from: WireJSON.encoder().encode(request))
        let expected = try WireJSON.decoder().decode(
            JSONValue.self,
            from: FixtureLoader.wireData(path: "dto/agent/StartScanRequest.json", case: "github_source_other_agent")
        )

        XCTAssertEqual(encoded, expected)
    }

    func testClaudeCodeGetsItsOptionsAndTheLinearFilterOnly() throws {
        let model = try makeModel()
        model.prepare(for: try workspace("auto_scan_choices"))
        model.source = .linear
        model.cli = "claude"

        let request = try XCTUnwrap(
            model.startRequest(
                workspaceName: "proj",
                claudeOptions: ClaudeOptionsModel(skip_permissions: false),
                initialPrompt: "Fix it."
            )
        )

        XCTAssertNil(request.github_filter)
        XCTAssertEqual(request.linear_filter?.state_names, ["Todo"])
        XCTAssertEqual(request.claude_options?.skip_permissions, false)
        XCTAssertNil(request.codex_options, "only the chosen agent's options are sent")
    }

    func testATemplateBringsBackTheChoicesAndKeepsOnlyTheChosenAgentsOptions() throws {
        let dialled = try makeModel()
        dialled.prepare(for: try workspace("auto_scan_nothing_chosen"))
        dialled.source = .github
        dialled.cli = "junie"
        // A template may keep a filter the step still has to finish.
        dialled.githubAssignee = " @me "
        dialled.toggleGitHubIssueType("Bug")
        dialled.junieOptions = JunieScanOptionsDto(plan_mode: true, brave: false)
        dialled.codexOptions.sandbox = "read-only"
        let other = try makeModel()
        other.prepare(for: try workspace("auto_scan_choices"))
        var appliedPrompt: String?
        other.applyInitialPrompt = { appliedPrompt = $0 }

        let captured = dialled.templateSettings(claudeOptions: ClaudeScanOptionsDto(), initialPrompt: "Fix it.")
        let settings = try WireJSON.decoder().decode(
            AutoScanSettingsDto.self,
            from: WireJSON.encoder().encode(captured)
        )
        other.apply(settings)

        XCTAssertEqual(settings.junie_options, JunieScanOptionsDto(plan_mode: true, brave: false))
        XCTAssertNil(settings.codex_options)
        XCTAssertNil(settings.claude_options)
        XCTAssertEqual(other.source, .github)
        XCTAssertEqual(other.cli, "junie")
        XCTAssertEqual(other.githubFilter, GitHubScanFilterDto(labels: [], assignee: "@me", issue_types: ["Bug"]))
        XCTAssertEqual(appliedPrompt, "Fix it.")
        XCTAssertEqual(other.junieOptions, JunieScanOptionsDto(plan_mode: true, brave: false))
        // Options the template does not hold stay as the step had them.
        XCTAssertEqual(other.codexOptions.model, "gpt-5.5")
    }

    func testAnAgentWithoutSavedOptionsRunsWithoutAskingAndAnotherWorkspaceStartsOver() throws {
        let model = try makeModel()
        model.prepare(for: try workspace("auto_scan_choices"))

        XCTAssertEqual(model.copilotOptions, CopilotScanOptionsDto(allow_all: false))
        XCTAssertEqual(model.cursorOptions, CursorScanOptionsDto(force: true))
        XCTAssertTrue(model.junieOptions.brave)

        model.junieOptions.brave = false
        model.prepare(for: try workspace("auto_scan_nothing_chosen"))

        XCTAssertEqual(model.codexOptions, CodexScanOptionsDto())
        XCTAssertEqual(model.copilotOptions, CopilotScanOptionsDto(allow_all: true))
        XCTAssertTrue(model.junieOptions.brave)
    }

    func testOptionsTheClientCannotReadDecodeAsNilAndTheWorkspaceStillDecodes() throws {
        let json = #"""
        {"name": "alpha", "path": "/p/alpha", "auto_scan": {
          "source": "github", "cli": "codex",
          "codex_options": {"full_auto": [1]},
          "copilot_options": {"allow_all": false}
        }}
        """#

        let workspace = try WireJSON.decoder().decode(WorkspaceInfo.self, from: Data(json.utf8))

        let saved = try XCTUnwrap(workspace.auto_scan)
        XCTAssertNil(saved.codex_options)
        XCTAssertEqual(saved.copilot_options, CopilotScanOptionsDto(allow_all: false))
        XCTAssertEqual(saved.cli, "codex")
    }

    func testSavedCodexOptionsWithRetiredUntrustedApprovalDecodeAsOnRequest() throws {
        let json = #"""
        {"name": "alpha", "path": "/p/alpha", "auto_scan": {
          "source": "github", "cli": "codex",
          "codex_options": {"sandbox": "read-only", "approval": "untrusted", "full_auto": false}
        }}
        """#

        let workspace = try WireJSON.decoder().decode(WorkspaceInfo.self, from: Data(json.utf8))

        let codex = try XCTUnwrap(workspace.auto_scan?.codex_options)
        XCTAssertEqual(codex.approval, "on-request")
        XCTAssertEqual(codex.sandbox, "read-only")
        XCTAssertFalse(codex.full_auto)
    }

    /// The fixture probe, plus `capabilities.chat` listing Codex when `codexChat`.
    private func probedModel(codexChat: Bool) throws -> AutoScanSetupModel {
        let chat = codexChat ? #","chat":{"codex":{"resume":true}}"# : ""
        let wire = Data(#"""
        {"name":"mobaide_agent","server_version":"0.3.30","protocol_versions":[6,7],"min_client_version":6,
         "capabilities":{"cli":{"claude":{"available":true},"codex":{"available":true,"auto_review":true}},
          "autoscan":{"sources":["linear","github"],"clis":["claude","codex"],"github_setup":true}\#(chat)}}
        """#.utf8)
        let model = AutoScanSetupModel(
            agentAPI: AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
        )
        model.applyProbe(try WireJSON.decoder().decode(VersionInfoResponse.self, from: wire))
        return model
    }

    func testSavedCodeModeSeedsEachAgentAndClaudeCodeModeKeepsTheChatView() throws {
        // The workspace saved Code Mode on for Claude Code and Pi, off for
        // OpenCode, and the chat view.
        let wire = Data(#"""
        {"name":"mobaide_agent","server_version":"0.3.34","protocol_versions":[6,7],"min_client_version":6,
         "capabilities":{"cli":{"claude":{"available":true},"pi":{"available":true}},
          "autoscan":{"sources":["linear","github"],"clis":["claude","pi"],"github_setup":true},
          "chat":{"claude":{"resume":true},"pi":{"resume":true}}}}
        """#.utf8)
        let model = AutoScanSetupModel(
            agentAPI: AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
        )
        model.applyProbe(try WireJSON.decoder().decode(VersionInfoResponse.self, from: wire))
        model.prepare(for: try workspace("auto_scan_choices"))

        XCTAssertTrue(model.claudeCodeMode)
        XCTAssertFalse(model.opencodeOptions.code_mode)
        model.cli = "claude"
        // Code Mode does not lock Claude Code's chat view; the chat view ignores it.
        XCTAssertEqual(model.effectiveSessionView, .chat)
        XCTAssertNil(model.chatUnavailableReason("claude"))

        model.cli = "pi"
        let request = try XCTUnwrap(
            model.startRequest(workspaceName: "p", claudeOptions: ClaudeOptionsModel(), initialPrompt: "")
        )
        XCTAssertEqual(request.pi_options, PiScanOptionsDto(code_mode: true))
        XCTAssertEqual(request.ui, "chat")
        XCTAssertNil(request.claude_options)
    }

    func testTheStartSendsTheChatOnlyOnceTheProbeShowsTheAgentsChatView() throws {
        // The workspace saved Codex in the chat view, 15 minutes apart.
        let withChat = try probedModel(codexChat: true)
        withChat.prepare(for: try workspace("auto_scan_choices"))
        let withoutChat = try probedModel(codexChat: false)
        withoutChat.prepare(for: try workspace("auto_scan_choices"))

        let chat = try XCTUnwrap(
            withChat.startRequest(workspaceName: "p", claudeOptions: ClaudeOptionsModel(), initialPrompt: "")
        )
        // The PC refuses a chat scan for an agent without a chat view.
        let terminal = try XCTUnwrap(
            withoutChat.startRequest(workspaceName: "p", claudeOptions: ClaudeOptionsModel(), initialPrompt: "")
        )

        XCTAssertEqual(chat.ui, "chat")
        XCTAssertEqual(chat.interval_minutes, 15)
        XCTAssertEqual(terminal.ui, "terminal")
        XCTAssertEqual(terminal.interval_minutes, 15)
        withChat.sessionView = .terminal
        XCTAssertEqual(
            withChat.startRequest(workspaceName: "p", claudeOptions: ClaudeOptionsModel(), initialPrompt: "")?.ui,
            "terminal"
        )
    }

    func testUnusableSavedViewsAndIntervalsKeepTheStepsChoice() throws {
        let model = try probedModel(codexChat: true)
        model.prepare(for: try workspace("auto_scan_choices"))
        XCTAssertEqual(model.sessionView, .chat)
        XCTAssertEqual(model.intervalMinutes, 15)

        // A template from another client, or a hand-edited one.
        model.apply(AutoScanSettingsDto(ui: "web", interval_minutes: 0))
        model.apply(AutoScanSettingsDto(ui: "Chat", interval_minutes: 1441))

        XCTAssertEqual(model.sessionView, .chat)
        XCTAssertEqual(model.intervalMinutes, 15)

        model.prepare(for: try workspace("auto_scan_nothing_chosen"))
        XCTAssertEqual(model.sessionView, .chat)
        XCTAssertEqual(model.intervalMinutes, AutoScanSetupModel.defaultIntervalMinutes)
    }

    func testCodexAgentDefaultEffortStaysNilThroughATemplate() throws {
        let dialled = try makeModel()
        dialled.prepare(for: try workspace("auto_scan_nothing_chosen"))
        dialled.source = .github
        dialled.cli = "codex"
        dialled.codexOptions.reasoning_effort = nil
        let other = try makeModel()
        other.prepare(for: try workspace("auto_scan_nothing_chosen"))

        let captured = dialled.templateSettings(claudeOptions: ClaudeScanOptionsDto(), initialPrompt: "")
        let settings = try WireJSON.decoder().decode(
            AutoScanSettingsDto.self,
            from: WireJSON.encoder().encode(captured)
        )
        other.apply(settings)

        // nil is Codex's own setting, not "no choice" to fill with "high".
        XCTAssertEqual(other.codexOptions.reasoning_effort, nil)
        XCTAssertNotNil(settings.codex_options)
    }

    func testTemplatesLoadFromThePCAndNamesMatchTheWayThePCMatchesThem() async throws {
        StubURLProtocol.reset()
        let wire = try FixtureLoader.wireData(
            path: "dto/agent/AutoScanTemplatesResponse.json",
            case: "two_templates"
        )
        StubURLProtocol.enqueue(status: 200, body: String(decoding: wire, as: UTF8.self))
        let model = AutoScanSetupModel(
            agentAPI: AgentAPI(
                baseURL: { "http://agent.example.com" },
                tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
                client: HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
            )
        )
        model.applyProbe(
            try FixtureLoader.decodeCase(
                VersionInfoResponse.self,
                path: "dto/agent/VersionInfoResponse.json",
                name: "autoscan_capability"
            )
        )

        // Without the list a same-name save would replace a template unwarned.
        XCTAssertFalse(model.canSaveTemplate)

        await model.loadTemplates()

        XCTAssertEqual(model.templatesState, .loaded)
        XCTAssertTrue(model.canSaveTemplate)
        XCTAssertEqual(model.existingTemplate(named: "  small \t BUGS ")?.id, "0f9e8d7c6b5a")
        XCTAssertNil(model.existingTemplate(named: "Small bug"))
        XCTAssertNil(AutoScanSetupModel.templateNameError(" " + String(repeating: "x", count: 60) + " "))
        XCTAssertNotNil(AutoScanSetupModel.templateNameError(" \n "))
        XCTAssertNotNil(AutoScanSetupModel.templateNameError(String(repeating: "x", count: 61)))
    }

    /// The PC counts code points, not what Swift calls characters: a family
    /// emoji is one `Character` but seven code points.
    func testATemplateNameIsCountedTheWayThePCCountsIt() {
        let family = "👨‍👩‍👧‍👦"
        XCTAssertEqual(family.count, 1)
        XCTAssertEqual(family.unicodeScalars.count, 7)

        XCTAssertNil(AutoScanSetupModel.templateNameError(String(repeating: family, count: 8) + "xxxx"))
        XCTAssertNotNil(AutoScanSetupModel.templateNameError(String(repeating: family, count: 8) + "xxxxx"))
    }

    /// A template may keep what Start refuses (no label yet, a label to add
    /// that is also a trigger label); only what the PC refuses blocks a save.
    func testATemplateSaveIsBlockedOnlyForWhatThePCRefuses() throws {
        let model = try makeModel()
        model.prepare(for: try workspace("auto_scan_nothing_chosen"))
        model.source = .github
        model.cli = "codex"
        model.antigravityAddDirsDraft = "-x"
        func saveError(prompt: String = "") -> String? {
            AutoScanSetupModel.templateSettingsError(
                model.templateSettings(claudeOptions: ClaudeScanOptionsDto(), initialPrompt: prompt)
            )
        }
        let long = String(repeating: "x", count: 101)

        XCTAssertNil(saveError(), "no label yet, and the directories of an agent not chosen")
        model.addGitHubLabel("ai-ready")
        model.githubActiveLabel = " AI-Ready "
        XCTAssertNil(saveError())
        XCTAssertNil(saveError(prompt: String(repeating: "p", count: 32_000)))
        XCTAssertNotNil(saveError(prompt: String(repeating: "p", count: 32_001)))

        for rejected in ["in,progress", long] {
            model.githubActiveLabel = rejected
            XCTAssertNotNil(saveError(), rejected)
        }
        model.githubActiveLabel = ""
        model.githubAssignee = long
        XCTAssertNotNil(saveError())
        model.githubAssignee = ""
        model.toggleGitHubIssueType(long)
        XCTAssertNotNil(saveError())
        model.toggleGitHubIssueType(long)
        XCTAssertNil(saveError())

        model.cli = "antigravity"
        XCTAssertNotNil(saveError())
    }

    /// Another client deleted the template first: the PC answers 404 and the
    /// result is the same as a delete.
    func testATemplateAlreadyGoneOnThePCIsDroppedWithoutAnError() async throws {
        StubURLProtocol.reset()
        let wire = try FixtureLoader.wireData(
            path: "dto/agent/AutoScanTemplatesResponse.json",
            case: "two_templates"
        )
        StubURLProtocol.enqueue(status: 200, body: String(decoding: wire, as: UTF8.self))
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Template '0f9e8d7c6b5a' not found"}"#)
        let model = AutoScanSetupModel(
            agentAPI: AgentAPI(
                baseURL: { "http://agent.example.com" },
                tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
                client: HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
            )
        )
        model.applyProbe(
            try FixtureLoader.decodeCase(
                VersionInfoResponse.self,
                path: "dto/agent/VersionInfoResponse.json",
                name: "autoscan_capability"
            )
        )
        await model.loadTemplates()
        let smallBugs = try XCTUnwrap(model.templates.first { $0.id == "0f9e8d7c6b5a" })
        model.applyTemplate(smallBugs)

        await model.deleteTemplate(smallBugs)

        XCTAssertEqual(model.templates.map(\.id), ["a1b2c3d4e5f6"])
        XCTAssertNil(model.appliedTemplateName)
        XCTAssertNil(model.templateActionError)
    }
}
