import Foundation
import Observation

/// Issue source, filter and agent choices of the wizard's Auto Scan step, for
/// an agent that advertises `capabilities.autoscan`. An older agent keeps the
/// Linear + Claude Code panel `CreateSessionModel` drives on its own.
///
/// The choices are seeded once per workspace from `WorkspaceInfo.auto_scan` (a
/// nil filter seeds empty fields) and live as long as the wizard, so a trip to
/// the GitHub setup terminal and back keeps every edit.
@MainActor
@Observable
final class AutoScanSetupModel {
    enum Source: String, CaseIterable, Sendable {
        case github
        case linear

        var title: String {
            switch self {
            case .github: return "GitHub Issues"
            case .linear: return "Linear"
            }
        }

        var subtitle: String {
            switch self {
            case .github: return "Open issues of this workspace's GitHub repository, read with the GitHub CLI on the PC."
            case .linear: return "Issues of your Linear team, read with this workspace's Linear API key."
            }
        }
    }

    enum GitHubStatusState: Equatable {
        case idle
        case loading
        case loaded(GitHubStatusResponse)
        case failed(String)
    }

    static let maxGitHubLabels = 20
    static let maxGitHubLabelLength = 100
    static let maxInitialPromptLength = 32_000
    private static let maxLabelSuggestions = 30

    /// nil for an agent without the capability, and until a probe answers.
    private(set) var capability: AutoScanCapability?
    /// Set once a probe has answered or failed, so the step can tell "older
    /// agent" from "still asking".
    private(set) var capabilityProbed = false
    private var cliAvailability: [String: Bool] = [:]

    /// The workspace the choices belong to, kept current with the live list.
    private(set) var workspace: WorkspaceInfo?
    var source: Source?
    var cli: String?

    // GitHub
    private(set) var githubStatus: GitHubStatusState = .idle
    private(set) var githubChecking = false
    private(set) var githubRepoLabels: [GitHubLabelDto] = []
    private(set) var githubRepoLabelsError: String?
    private(set) var githubLabels: [String] = []
    var githubLabelDraft = "" {
        didSet { if githubLabelDraft != oldValue { githubLabelError = nil } }
    }
    private(set) var githubLabelError: String?
    var githubAssignee = ""
    var githubActiveLabel = ""

    // Linear
    var linearTriggerStates: Set<String> = []
    var linearTriggerLabels: Set<String> = []
    var linearActiveState = ""
    var linearDoneState = ""
    var linearRemovalLabel = ""
    private(set) var linearRepoLabels: [LinearLabelDto] = []
    private(set) var linearRepoStates: [LinearStateDto] = []
    private(set) var linearMetadataLoading = false
    private(set) var linearMetadataError: String?
    var linearKeyDraft = ""
    private(set) var savingLinearKey = false
    private(set) var linearKeyError: String?
    /// The key was saved from the wizard; the `projects.changed` echo that
    /// flips `has_linear_key` may still be on its way.
    private var linearKeySaved = false

    private var seededFor: String?
    private var linearMetadataLoadedFor: String?
    private var statusGeneration = 0
    private var labelsGeneration = 0
    private var linearGeneration = 0
    private let agentAPI: AgentAPI

    init(agentAPI: AgentAPI) {
        self.agentAPI = agentAPI
    }

    // MARK: - Capability

    var isAvailable: Bool { capability != nil }

    /// A failed probe keeps the last answer.
    func applyProbe(_ version: VersionInfoResponse?) {
        capabilityProbed = true
        guard let version else { return }
        capability = version.autoScanCapability
        var availability: [String: Bool] = [:]
        for kind in capability?.clis ?? [] {
            availability[kind] = version.cliAvailable(kind) == true
        }
        cliAvailability = availability
    }

    /// GitHub first, as the wizard lists them.
    var sources: [Source] {
        let advertised = Set(capability?.sources ?? [])
        return Source.allCases.filter { advertised.contains($0.rawValue) }
    }

    var agents: [String] { capability?.clis ?? [] }

    func isAgentAvailable(_ kind: String) -> Bool { cliAvailability[kind] == true }

    /// `POST /api/autoscan/github/setup` exists: the install and sign-in
    /// buttons can open a terminal on the PC.
    var canRunGitHubSetup: Bool { capability?.githubSetup == true }

    // MARK: - Workspace

    /// Seeds the choices from the workspace's saved ones the first time the
    /// step opens for it. A later call for the same workspace only refreshes
    /// the row, so the edits made in the wizard survive.
    func prepare(for workspace: WorkspaceInfo) {
        self.workspace = workspace
        guard seededFor != workspace.name else { return }
        seededFor = workspace.name
        let saved = workspace.auto_scan
        source = saved?.source.flatMap(Source.init(rawValue:))
        cli = saved?.cli

        githubLabels = saved?.github_filter?.labels ?? []
        githubAssignee = saved?.github_filter?.assignee ?? ""
        githubActiveLabel = saved?.github_filter?.active_label ?? ""
        githubLabelDraft = ""
        githubLabelError = nil
        statusGeneration += 1
        labelsGeneration += 1
        githubStatus = .idle
        githubChecking = false
        githubRepoLabels = []
        githubRepoLabelsError = nil

        let linear = saved?.linear_filter
        linearTriggerStates = Set(linear?.state_names ?? [])
        linearTriggerLabels = Set(linear?.label_names ?? [])
        linearActiveState = linear?.active_state ?? ""
        linearDoneState = linear?.done_state ?? ""
        linearRemovalLabel = linear?.removal_label ?? ""
        linearGeneration += 1
        linearMetadataLoadedFor = nil
        linearMetadataLoading = false
        linearMetadataError = nil
        linearRepoLabels = []
        linearRepoStates = []
        linearKeyDraft = ""
        linearKeyError = nil
        linearKeySaved = false
    }

    /// Keeps `workspace` in step with the agent's live list (its Linear key
    /// flag can change while the step is open).
    func workspacesChanged(_ workspaces: [WorkspaceInfo]) {
        guard let name = seededFor, let row = workspaces.first(where: { $0.name == name }) else { return }
        workspace = row
    }

    var hasLinearKey: Bool { workspace?.has_linear_key == true || linearKeySaved }

    // MARK: - GitHub

    var githubReadyStatus: GitHubStatusResponse? {
        if case .loaded(let status) = githubStatus, status.problem == nil { return status }
        return nil
    }

    /// `GET /api/autoscan/github/status`, then the repository's labels once a
    /// scan can start. The last answer stays on screen while a re-check runs.
    func refreshGitHubStatus() async {
        guard let name = workspace?.name else { return }
        statusGeneration += 1
        let generation = statusGeneration
        githubChecking = true
        if case .failed = githubStatus { githubStatus = .loading }
        if case .idle = githubStatus { githubStatus = .loading }
        do {
            let status = try await agentAPI.getGitHubScanStatus(workspaceName: name)
            guard generation == statusGeneration else { return }
            githubStatus = .loaded(status)
            githubChecking = false
            if status.problem == nil, githubRepoLabels.isEmpty {
                await loadGitHubLabels()
            }
        } catch {
            guard generation == statusGeneration else { return }
            githubChecking = false
            // The step left the screen mid-check; the next appearance checks again.
            if Task.isCancelled {
                if case .loading = githubStatus { githubStatus = .idle }
                return
            }
            githubStatus = .failed(Self.detail(of: error) ?? "Couldn't check GitHub on the PC.")
        }
    }

    func loadGitHubLabels() async {
        guard let name = workspace?.name else { return }
        labelsGeneration += 1
        let generation = labelsGeneration
        githubRepoLabelsError = nil
        do {
            let response = try await agentAPI.getGitHubScanLabels(workspaceName: name)
            guard generation == labelsGeneration else { return }
            githubRepoLabels = response.labels
        } catch {
            guard generation == labelsGeneration, !Task.isCancelled else { return }
            githubRepoLabelsError = Self.detail(of: error) ?? "Couldn't load the repository's labels."
        }
    }

    /// The repository's labels not chosen yet, filtered by the typed text.
    private var githubLabelCandidates: [GitHubLabelDto] {
        let query = githubLabelDraft.trimmingCharacters(in: .whitespaces).lowercased()
        return githubRepoLabels.filter { label in
            !githubLabels.contains(label.name)
                && (query.isEmpty || label.name.lowercased().contains(query))
        }
    }

    /// The first matching labels shown as chips; the rest are found by typing.
    var githubLabelSuggestions: [GitHubLabelDto] {
        Array(githubLabelCandidates.prefix(Self.maxLabelSuggestions))
    }

    /// How many matching labels the chips leave out.
    var githubHiddenLabelSuggestionCount: Int {
        max(0, githubLabelCandidates.count - Self.maxLabelSuggestions)
    }

    /// Adds a trigger label with the agent's rules: trimmed, no comma (`gh`
    /// splits option values on commas), at most 100 characters, 20 labels.
    func addGitHubLabel(_ raw: String) {
        let label = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !label.isEmpty else { return }
        if let error = Self.githubLabelError(label) {
            githubLabelError = error
            return
        }
        if !githubLabels.contains(label) {
            guard githubLabels.count < Self.maxGitHubLabels else {
                githubLabelError = "You can add at most \(Self.maxGitHubLabels) labels."
                return
            }
            githubLabels.append(label)
        }
        githubLabelDraft = ""
        githubLabelError = nil
    }

    func submitGitHubLabelDraft() {
        addGitHubLabel(githubLabelDraft)
    }

    func removeGitHubLabel(_ label: String) {
        githubLabels.removeAll { $0 == label }
        githubLabelError = nil
    }

    var githubFilter: GitHubScanFilterDto {
        GitHubScanFilterDto(
            labels: githubLabels,
            assignee: Self.blankToNil(githubAssignee),
            active_label: Self.blankToNil(githubActiveLabel)
        )
    }

    // MARK: - Linear

    var linearLabelOptions: [String] {
        LinearFilterOptions.labels(
            discovered: linearRepoLabels,
            selected: linearTriggerLabels,
            extra: [linearRemovalLabel]
        )
    }

    var linearStateOptions: [LinearStateDto] {
        LinearFilterOptions.states(
            discovered: linearRepoStates,
            selected: linearTriggerStates,
            extra: [linearActiveState, linearDoneState]
        )
    }

    var linearFilter: AutoScanFilterDto {
        AutoScanFilterDto(
            state_names: linearStateOptions.map(\.name).filter { linearTriggerStates.contains($0) },
            label_names: linearLabelOptions.filter { linearTriggerLabels.contains($0) },
            active_state: linearActiveState.trimmingCharacters(in: .whitespacesAndNewlines),
            done_state: linearDoneState.trimmingCharacters(in: .whitespacesAndNewlines),
            removal_label: linearRemovalLabel.trimmingCharacters(in: .whitespacesAndNewlines)
        )
    }

    /// Saves the key on the workspace (`PUT /api/projects/{name}`), the same
    /// way the workspace form does; Linear's labels and states load next.
    func saveLinearKey() async {
        let key = linearKeyDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let name = workspace?.name, !key.isEmpty, !savingLinearKey else { return }
        savingLinearKey = true
        linearKeyError = nil
        defer { savingLinearKey = false }
        do {
            let updated = try await agentAPI.updateWorkspace(
                currentName: name,
                update: WorkspaceUpdate(linear_api_key: key)
            )
            guard seededFor == name else { return }
            workspace = updated
            linearKeySaved = true
            linearKeyDraft = ""
        } catch {
            linearKeyError = Self.detail(of: error) ?? "Couldn't save the key."
        }
    }

    /// Once per workspace; `force` re-reads after a failure or a refresh tap.
    func loadLinearMetadata(force: Bool = false) async {
        guard let name = workspace?.name, hasLinearKey else { return }
        if !force, linearMetadataLoadedFor == name { return }
        linearMetadataLoadedFor = name
        linearGeneration += 1
        let generation = linearGeneration
        linearMetadataLoading = true
        linearMetadataError = nil
        let agentAPI = self.agentAPI
        async let labels = agentAPI.getLinearLabels(workspaceName: name)
        async let states = agentAPI.getLinearStates(workspaceName: name)
        do {
            let (loadedLabels, loadedStates) = try await (labels, states)
            guard generation == linearGeneration else { return }
            linearRepoLabels = loadedLabels
            linearRepoStates = loadedStates
            linearMetadataLoading = false
        } catch {
            guard generation == linearGeneration else { return }
            linearMetadataLoading = false
            if Task.isCancelled {
                linearMetadataLoadedFor = nil
                return
            }
            linearMetadataError = "Couldn’t load Linear labels/states."
        }
    }

    /// Why the agent would refuse this label name; nil when it is fine.
    static func githubLabelError(_ label: String) -> String? {
        if label.contains(",") { return "A label name cannot contain a comma." }
        if label.unicodeScalars.count > maxGitHubLabelLength {
            return "A label name can have at most \(maxGitHubLabelLength) characters."
        }
        return nil
    }

    // MARK: - Start

    /// The first reason the scan cannot start yet, with the initial prompt the
    /// user typed; nil when it can.
    func startBlocker(prompt: String) -> String? {
        if let blocker = startBlocker { return blocker }
        if prompt.unicodeScalars.count > Self.maxInitialPromptLength {
            return "The initial prompt can have at most 32,000 characters."
        }
        return nil
    }

    /// The first reason the scan cannot start yet, shown under Start; nil
    /// when it can.
    var startBlocker: String? {
        guard let source else { return "Choose where to find issues." }
        switch source {
        case .github:
            switch githubStatus {
            case .idle, .loading:
                return "Checking GitHub on the PC…"
            case .failed:
                return "Couldn't check GitHub. Tap Check again."
            case .loaded(let status):
                if status.problem != nil { return "Set up GitHub above, then tap Check again." }
            }
            if githubLabels.isEmpty { return "Add at least one label to pick up." }
            let assignee = githubAssignee.trimmingCharacters(in: .whitespacesAndNewlines)
            if assignee.unicodeScalars.count > Self.maxGitHubLabelLength {
                return "The assignee can have at most \(Self.maxGitHubLabelLength) characters."
            }
            let active = githubActiveLabel.trimmingCharacters(in: .whitespacesAndNewlines)
            if !active.isEmpty, let error = Self.githubLabelError(active) {
                return "Label to add: \(error)"
            }
        case .linear:
            if !hasLinearKey { return "Save a Linear API key for this workspace." }
            if linearTriggerStates.isEmpty { return "Choose at least one trigger state." }
            if linearTriggerLabels.isEmpty { return "Choose at least one trigger label." }
            if Self.isBlank(linearActiveState) { return "Choose the active state." }
            if Self.isBlank(linearDoneState) { return "Choose the done state." }
            if Self.isBlank(linearRemovalLabel) { return "Choose the removal label." }
        }
        guard let cli, agents.contains(cli) else { return "Choose the AI agent." }
        guard isAgentAvailable(cli) else {
            return "\(AutoScanDisplay.agentName(cli)) is not installed on the PC."
        }
        return nil
    }

    /// The start body: every choice, the chosen source's filter only, Claude
    /// Code's options only for Claude Code and `auto_approve` for the others.
    func startRequest(
        workspaceName: String,
        claudeOptions: ClaudeOptionsModel,
        autoApprove: Bool,
        initialPrompt: String
    ) -> StartScanRequest? {
        guard let source, let cli else { return nil }
        let isClaude = cli == "claude"
        return StartScanRequest(
            workspace_name: workspaceName,
            claude_options: isClaude ? claudeOptions : nil,
            source: source.rawValue,
            cli: cli,
            auto_approve: isClaude ? nil : autoApprove,
            initial_prompt: initialPrompt,
            linear_filter: source == .linear ? linearFilter : nil,
            github_filter: source == .github ? githubFilter : nil
        )
    }

    // MARK: - Helpers

    private static func isBlank(_ value: String) -> Bool {
        value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private static func blankToNil(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private static func detail(of error: Error) -> String? {
        guard let apiError = error as? APIError else { return nil }
        return ConflictDetail.stringOrNil(from: apiError.errorBody)
    }
}
