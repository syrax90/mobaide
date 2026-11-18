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

    /// The saved GitHub choices, with the PC's `gh` checked and ready.
    private func checkedGitHubModel() async throws -> AutoScanSetupModel {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"installed":true,"version":"2.83.0","signed_in":true,"account":"octocat","host":"github.com","repo":"acme/app","repo_url":"https://github.com/acme/app","problem":null,"can_install":false,"install_command":null}"#
        )
        StubURLProtocol.enqueue(status: 200, body: #"{"labels":[]}"#)
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
        XCTAssertEqual(model.githubFilter, GitHubScanFilterDto(labels: ["ai-ready", "bot"], assignee: "@me"))
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

    func testTheGitHubStartBodyEncodesToTheFixtureWire() throws {
        let model = try makeModel()
        model.prepare(for: try workspace("auto_scan_choices"))
        model.githubActiveLabel = " ai-doing "

        let request = try XCTUnwrap(
            model.startRequest(
                workspaceName: "p",
                claudeOptions: ClaudeOptionsModel(),
                autoApprove: false,
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
                autoApprove: true,
                initialPrompt: "Fix it."
            )
        )

        XCTAssertNil(request.github_filter)
        XCTAssertEqual(request.linear_filter?.state_names, ["Todo"])
        XCTAssertEqual(request.claude_options?.skip_permissions, false)
        XCTAssertNil(request.auto_approve, "Claude Code reads claude_options.skip_permissions")
    }
}
