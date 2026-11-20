import Foundation
import Observation

/// Issue source, filter, agent and agent-option choices of the wizard's Auto
/// Scan step, and its templates, for an agent that advertises
/// `capabilities.autoscan`. An older agent keeps the Linear + Claude Code panel
/// `CreateSessionModel` drives on its own.
///
/// The choices are seeded once per workspace from `WorkspaceInfo.auto_scan` (a
/// nil filter seeds empty fields) and live as long as the wizard, so a trip to
/// the GitHub setup terminal and back keeps every edit. Seeding and applying a
/// template both run `apply(_:)`; Claude Code's options and the initial prompt
/// live on `CreateSessionModel` (its older-agent panel uses them too), which
/// connects them through `applyClaudeOptions` and `applyInitialPrompt`.
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

    /// How each issue's session opens.
    enum SessionView: String, CaseIterable, Sendable {
        case terminal
        case chat

        var title: String {
            switch self {
            case .terminal: return "Terminal"
            case .chat: return "Chat"
            }
        }
    }

    enum GitHubStatusState: Equatable {
        case idle
        case loading
        case loaded(GitHubStatusResponse)
        case failed(String)
    }

    enum TemplatesState: Equatable {
        case idle
        case loading
        case loaded
        case failed(String)
    }

    static let maxGitHubLabels = 20
    static let maxGitHubIssueTypes = 20
    static let maxGitHubLabelLength = 100
    static let maxInitialPromptLength = 32_000
    static let maxTemplateNameLength = 60
    /// Above this many repository labels a search field filters them.
    static let maxLabelSuggestions = 30
    /// The agent's own wait between cycles when a start sends none.
    static let defaultIntervalMinutes = 5
    static let intervalRange = 1...1440
    /// The "Time between scans" choices, in minutes.
    static let intervalChoices = [1, 2, 5, 10, 15, 30, 60, 120, 240, 480, 1440]

    /// nil for an agent without the capability, and until a probe answers.
    private(set) var capability: AutoScanCapability?
    /// Set once a probe has answered or failed, so the step can tell "older
    /// agent" from "still asking".
    private(set) var capabilityProbed = false
    private var cliAvailability: [String: Bool] = [:]
    /// `capabilities.chat` for each agent kind; empty until a probe answers.
    private var chatCapabilities: [String: ChatCapability] = [:]

    /// The workspace the choices belong to, kept current with the live list.
    private(set) var workspace: WorkspaceInfo?
    var source: Source?
    var cli: String?
    /// The chosen view; `effectiveSessionView` is the one a start sends.
    var sessionView: SessionView = .chat
    var intervalMinutes = AutoScanSetupModel.defaultIntervalMinutes

    // GitHub
    private(set) var githubStatus: GitHubStatusState = .idle
    private(set) var githubChecking = false
    private(set) var githubRepoLabels: [GitHubLabelDto] = []
    private(set) var githubRepoIssueTypes: [GitHubIssueTypeDto] = []
    /// The repository's labels and issue types have answered at least once.
    private(set) var githubLabelsLoaded = false
    private(set) var githubRepoLabelsError: String?
    private(set) var githubLabels: [String] = []
    /// Filters the repository's labels; it never adds a name.
    var githubLabelSearch = ""
    private(set) var githubLabelError: String?
    var githubAssignee = ""
    var githubActiveLabel = ""
    private(set) var githubIssueTypes: [String] = []

    // Agent options (Claude Code's live on `CreateSessionModel`). Defaults are
    // Auto Scan's: a scan runs unattended, so each agent runs without asking.
    var codexOptions = CodexScanOptionsDto()
    var antigravityOptions = AntigravityScanOptionsDto()
    /// The text the user types; `antigravityOptions.add_dirs` is parsed from it.
    var antigravityAddDirsDraft = ""
    var opencodeOptions = OpenCodeScanOptionsDto()
    var copilotOptions = CopilotScanOptionsDto()
    var cursorOptions = CursorScanOptionsDto()
    var junieOptions = JunieScanOptionsDto()

    /// Set by `CreateSessionModel`: applies a template's or a workspace's Claude
    /// Code options and a template's initial prompt.
    var applyClaudeOptions: ((ClaudeScanOptionsDto) -> Void)?
    var applyInitialPrompt: ((String) -> Void)?

    // Templates
    private(set) var templates: [AutoScanTemplateDto] = []
    private(set) var templatesState: TemplatesState = .idle
    /// The template the choices came from, until another is applied or saved.
    private(set) var appliedTemplateName: String?
    /// "Applied …" / "Saved …", shown under the template picker.
    private(set) var templateNotice: String?
    private(set) var templateActionError: String?
    private(set) var savingTemplate = false
    private(set) var deletingTemplateIds: Set<String> = []

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
        var chat: [String: ChatCapability] = [:]
        for kind in capability?.clis ?? [] {
            chat[kind] = version.chatCapability(kind)
        }
        chatCapabilities = chat
    }

    /// GitHub first, as the wizard lists them.
    var sources: [Source] {
        let advertised = Set(capability?.sources ?? [])
        return Source.allCases.filter { advertised.contains($0.rawValue) }
    }

    var agents: [String] { capability?.clis ?? [] }

    func isAgentAvailable(_ kind: String) -> Bool { cliAvailability[kind] == true }

    /// The agent can open its sessions in the chat view on this PC.
    func isChatAvailable(_ kind: String) -> Bool { chatCapabilities[kind]?.isAvailable == true }

    /// Why the agent cannot open its sessions in the chat view (or that the
    /// probe has not answered yet); nil when it can.
    func chatUnavailableReason(_ kind: String) -> String? {
        guard let capability = chatCapabilities[kind] else {
            return "Checking the chat view on the PC…"
        }
        return CreateSessionModel.chatUnavailableReason(capability, kind: kind)
    }

    /// The chosen view, or the terminal for an agent without the chat view.
    var effectiveSessionView: SessionView {
        guard sessionView == .chat, let cli, isChatAvailable(cli) else { return .terminal }
        return .chat
    }

    /// "5 min", "1 hour", "24 hours".
    static func intervalTitle(_ minutes: Int) -> String {
        if minutes >= 60, minutes % 60 == 0 {
            let hours = minutes / 60
            return hours == 1 ? "1 hour" : "\(hours) hours"
        }
        return "\(minutes) min"
    }

    /// The choices, with a saved value the list does not have (45 min, say).
    var intervalOptions: [Int] {
        Self.intervalChoices.contains(intervalMinutes)
            ? Self.intervalChoices
            : (Self.intervalChoices + [intervalMinutes]).sorted()
    }

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
        source = nil
        cli = nil
        sessionView = .chat
        intervalMinutes = Self.defaultIntervalMinutes

        githubLabels = []
        githubAssignee = ""
        githubActiveLabel = ""
        githubIssueTypes = []
        githubLabelSearch = ""
        githubLabelError = nil
        statusGeneration += 1
        labelsGeneration += 1
        githubStatus = .idle
        githubChecking = false
        githubRepoLabels = []
        githubRepoIssueTypes = []
        githubLabelsLoaded = false
        githubRepoLabelsError = nil

        linearTriggerStates = []
        linearTriggerLabels = []
        linearActiveState = ""
        linearDoneState = ""
        linearRemovalLabel = ""
        codexOptions = CodexScanOptionsDto()
        antigravityOptions = AntigravityScanOptionsDto()
        antigravityAddDirsDraft = ""
        opencodeOptions = OpenCodeScanOptionsDto()
        copilotOptions = CopilotScanOptionsDto()
        cursorOptions = CursorScanOptionsDto()
        junieOptions = JunieScanOptionsDto()
        appliedTemplateName = nil
        templateNotice = nil
        templateActionError = nil
        apply(workspace.auto_scan ?? AutoScanSettingsDto())
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
    /// scan can start: the first time, and on every `userAsked` check (Check
    /// again), so a label created on GitHub meanwhile shows up. The last answer
    /// stays on screen while a re-check runs.
    func refreshGitHubStatus(userAsked: Bool = false) async {
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
            if status.problem == nil, userAsked || githubRepoLabels.isEmpty {
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

    /// The repository's labels and issue types; `Try again` calls it too.
    func loadGitHubLabels() async {
        guard let name = workspace?.name else { return }
        labelsGeneration += 1
        let generation = labelsGeneration
        githubRepoLabelsError = nil
        do {
            let response = try await agentAPI.getGitHubScanLabels(workspaceName: name)
            guard generation == labelsGeneration else { return }
            githubRepoLabels = response.labels
            githubRepoIssueTypes = response.issue_types
            githubLabelsLoaded = true
        } catch {
            guard generation == labelsGeneration, !Task.isCancelled else { return }
            githubRepoLabelsError = Self.detail(of: error) ?? "Couldn't load the repository's labels."
        }
    }

    /// More repository labels than the chips show: the search field appears.
    var githubLabelSearchShown: Bool { githubRepoLabels.count > Self.maxLabelSuggestions }

    /// The repository's labels not chosen yet, filtered by the search text.
    private var githubLabelCandidates: [GitHubLabelDto] {
        let query = githubLabelSearch.trimmingCharacters(in: .whitespaces).lowercased()
        return githubRepoLabels.filter { label in
            !githubLabels.contains(label.name)
                && (query.isEmpty || label.name.lowercased().contains(query))
        }
    }

    /// The first matching labels shown as chips; the rest are found by searching.
    var githubLabelSuggestions: [GitHubLabelDto] {
        Array(githubLabelCandidates.prefix(Self.maxLabelSuggestions))
    }

    /// How many matching labels the chips leave out.
    var githubHiddenLabelSuggestionCount: Int {
        max(0, githubLabelCandidates.count - Self.maxLabelSuggestions)
    }

    /// Picks up a repository label, with the agent's rules: no comma (`gh`
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
                githubLabelError = "You can pick up at most \(Self.maxGitHubLabels) labels."
                return
            }
            githubLabels.append(label)
        }
        githubLabelError = nil
    }

    func removeGitHubLabel(_ label: String) {
        githubLabels.removeAll { $0 == label }
        githubLabelError = nil
    }

    /// The repository's issue types, then any chosen one it does not list (from
    /// a template, say), so it can still be unchosen.
    var githubIssueTypeOptions: [String] {
        let listed = githubRepoIssueTypes.map(\.name)
        let extra = githubIssueTypes.filter { chosen in
            !listed.contains { $0.caseInsensitiveCompare(chosen) == .orderedSame }
        }
        return listed + extra
    }

    func isGitHubIssueTypeChosen(_ name: String) -> Bool {
        githubIssueTypes.contains { $0.caseInsensitiveCompare(name) == .orderedSame }
    }

    func toggleGitHubIssueType(_ name: String) {
        if isGitHubIssueTypeChosen(name) {
            githubIssueTypes.removeAll { $0.caseInsensitiveCompare(name) == .orderedSame }
        } else if githubIssueTypes.count < Self.maxGitHubIssueTypes {
            githubIssueTypes.append(name)
        }
    }

    var githubFilter: GitHubScanFilterDto {
        GitHubScanFilterDto(
            labels: githubLabels,
            assignee: Self.blankToNil(githubAssignee),
            active_label: Self.blankToNil(githubActiveLabel),
            issue_types: githubIssueTypes
        )
    }

    // MARK: - Agent options

    func setAntigravitySkipPermissions(_ on: Bool) {
        antigravityOptions.skip_permissions = on
        if on { antigravityOptions.sandbox = false }
    }

    func setAntigravitySandbox(_ on: Bool) {
        antigravityOptions.sandbox = on
        if on { antigravityOptions.skip_permissions = false }
    }

    /// The Antigravity options as sent: `add_dirs` parsed from the draft.
    var antigravityOptionsForStart: AntigravityScanOptionsDto {
        var options = antigravityOptions
        options.add_dirs = CreateSessionModel.parseAddDirs(antigravityAddDirsDraft)
        return options
    }

    /// Whether the chosen agent's "run without asking" switch is off, so it
    /// may stop and wait for an approval. Codex with approval `never` never asks.
    func agentMayAsk(_ kind: String) -> Bool {
        switch kind {
        case "codex": return !codexOptions.full_auto && codexOptions.approval != "never"
        case "antigravity": return !antigravityOptions.skip_permissions
        case "opencode": return !opencodeOptions.auto_approve
        case "copilot": return !copilotOptions.allow_all
        case "cursor": return !cursorOptions.force
        case "junie": return !junieOptions.brave
        default: return false
        }
    }

    // MARK: - Settings (seeding, templates)

    /// Applies saved choices: every non-nil field replaces the current one.
    /// The one path for a workspace's saved choices and for a template.
    func apply(_ settings: AutoScanSettingsDto) {
        if let saved = settings.source.flatMap(Source.init(rawValue:)) { source = saved }
        if let saved = settings.cli { cli = saved }
        if let saved = settings.ui.flatMap(SessionView.init(rawValue:)) { sessionView = saved }
        if let saved = settings.interval_minutes, Self.intervalRange.contains(saved) {
            intervalMinutes = saved
        }
        if let github = settings.github_filter {
            githubLabels = Array(github.labels.prefix(Self.maxGitHubLabels))
            githubAssignee = github.assignee ?? ""
            githubActiveLabel = github.active_label ?? ""
            githubIssueTypes = github.issue_types
            githubLabelError = nil
        }
        if let linear = settings.linear_filter {
            linearTriggerStates = Set(linear.state_names)
            linearTriggerLabels = Set(linear.label_names)
            linearActiveState = linear.active_state
            linearDoneState = linear.done_state
            linearRemovalLabel = linear.removal_label
        }
        if let codex = settings.codex_options { codexOptions = codex }
        if let antigravity = settings.antigravity_options {
            antigravityOptions = antigravity
            if antigravity.skip_permissions { antigravityOptions.sandbox = false }
            antigravityAddDirsDraft = antigravity.add_dirs.joined(separator: "\n")
        }
        if let options = settings.opencode_options { opencodeOptions = options }
        if let options = settings.copilot_options { copilotOptions = options }
        if let options = settings.cursor_options { cursorOptions = options }
        if let options = settings.junie_options { junieOptions = options }
        if let options = settings.claude_options { applyClaudeOptions?(options) }
        if let prompt = settings.initial_prompt { applyInitialPrompt?(prompt) }
    }

    /// The current choices as a template keeps them: both filters as they
    /// stand (possibly incomplete) and only the chosen agent's options.
    func templateSettings(claudeOptions: ClaudeScanOptionsDto, initialPrompt: String) -> AutoScanSettingsDto {
        AutoScanSettingsDto(
            source: source?.rawValue,
            cli: cli,
            // The view the user chose: a template may run on a PC where the
            // agent has the chat view. A start sends the effective one.
            ui: sessionView.rawValue,
            interval_minutes: intervalMinutes,
            initial_prompt: initialPrompt,
            linear_filter: linearFilter,
            github_filter: githubFilter,
            claude_options: cli == "claude" ? claudeOptions : nil,
            codex_options: cli == "codex" ? codexOptions : nil,
            antigravity_options: cli == "antigravity" ? antigravityOptionsForStart : nil,
            opencode_options: cli == "opencode" ? opencodeOptions : nil,
            copilot_options: cli == "copilot" ? copilotOptions : nil,
            cursor_options: cli == "cursor" ? cursorOptions : nil,
            junie_options: cli == "junie" ? junieOptions : nil
        )
    }

    // MARK: - Templates

    /// Once per wizard; `force` reads again (Try again).
    func loadTemplates(force: Bool = false) async {
        guard isAvailable else { return }
        switch templatesState {
        case .loading: return
        case .loaded where !force: return
        default: break
        }
        templatesState = .loading
        do {
            templates = try await agentAPI.getAutoScanTemplates().templates
            templatesState = .loaded
        } catch {
            if Task.isCancelled {
                templatesState = .idle
                return
            }
            templatesState = .failed(Self.detail(of: error) ?? "Couldn't load the templates.")
        }
    }

    /// An old save or delete error must not outlive the sheet or reappear in it.
    func clearTemplateActionError() {
        templateActionError = nil
    }

    func applyTemplate(_ template: AutoScanTemplateDto) {
        apply(template.settings)
        appliedTemplateName = template.name
        templateNotice = "Applied “\(template.name)”."
        templateActionError = nil
    }

    /// Saving needs the list: without it a same-name save would replace a
    /// template with no "Replaces …" warning.
    var canSaveTemplate: Bool { templatesState == .loaded && !savingTemplate }

    /// Runs of white space become one space, as the agent stores the name.
    static func normalizedTemplateName(_ raw: String) -> String {
        raw.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }

    /// The template a save under `name` would replace (ignoring case and extra
    /// spaces), or nil.
    func existingTemplate(named name: String) -> AutoScanTemplateDto? {
        let key = Self.normalizedTemplateName(name).lowercased()
        guard !key.isEmpty else { return nil }
        return templates.first { Self.normalizedTemplateName($0.name).lowercased() == key }
    }

    /// Why `name` cannot be saved, or nil.
    static func templateNameError(_ name: String) -> String? {
        let normalized = normalizedTemplateName(name)
        if normalized.isEmpty { return "Type a name." }
        // The agent counts code points.
        if normalized.unicodeScalars.count > maxTemplateNameLength {
            return "A name can have at most \(maxTemplateNameLength) characters."
        }
        return nil
    }

    /// What the agent's template models would refuse in `settings`, naming the
    /// field; nil when it can be saved. A half-made filter is fine, and so is a
    /// label to add that is also a trigger label (Start blocks that one).
    static func templateSettingsError(_ settings: AutoScanSettingsDto) -> String? {
        if let github = settings.github_filter {
            for label in github.labels {
                if label.contains(",") { return "A label to pick up cannot contain a comma." }
                if label.unicodeScalars.count > maxGitHubLabelLength {
                    return "A label to pick up can have at most \(maxGitHubLabelLength) characters."
                }
            }
            if let active = github.active_label {
                if active.contains(",") { return "The label to add cannot contain a comma." }
                if active.unicodeScalars.count > maxGitHubLabelLength {
                    return "The label to add can have at most \(maxGitHubLabelLength) characters."
                }
            }
            if github.issue_types.contains(where: { $0.unicodeScalars.count > maxGitHubLabelLength }) {
                return "An issue type can have at most \(maxGitHubLabelLength) characters."
            }
            if (github.assignee ?? "").unicodeScalars.count > maxGitHubLabelLength {
                return "The assignee can have at most \(maxGitHubLabelLength) characters."
            }
        }
        if (settings.initial_prompt ?? "").unicodeScalars.count > maxInitialPromptLength {
            return "The instructions can have at most 32,000 characters."
        }
        if settings.cli == "antigravity",
           let dirs = settings.antigravity_options?.add_dirs,
           let error = CreateSessionModel.addDirsError(dirs.joined(separator: "\n")) {
            return "Additional directories: \(error)."
        }
        return nil
    }

    /// Saves `settings` as `name`; on failure the error stays in
    /// `templateActionError` and the answer is false.
    func saveTemplate(name: String, settings: AutoScanSettingsDto) async -> Bool {
        let normalized = Self.normalizedTemplateName(name)
        guard Self.templateNameError(normalized) == nil, !savingTemplate else { return false }
        if let error = Self.templateSettingsError(settings) {
            templateActionError = error
            return false
        }
        savingTemplate = true
        templateActionError = nil
        defer { savingTemplate = false }
        do {
            let response = try await agentAPI.saveAutoScanTemplate(
                SaveAutoScanTemplateRequest(name: normalized, settings: settings)
            )
            templates = response.templates
            templatesState = .loaded
            appliedTemplateName = normalized
            templateNotice = "Saved “\(normalized)”."
            return true
        } catch let apiError as APIError where apiError.code == 422 {
            templateActionError = Self.detail(of: apiError)
                ?? "The PC did not accept these settings. Check the filter and the agent options."
            return false
        } catch {
            templateActionError = Self.detail(of: error) ?? Self.httpFallback(error, "Couldn't save the template.")
            return false
        }
    }

    func deleteTemplate(_ template: AutoScanTemplateDto) async {
        guard !deletingTemplateIds.contains(template.id) else { return }
        deletingTemplateIds.insert(template.id)
        templateActionError = nil
        defer { deletingTemplateIds.remove(template.id) }
        do {
            templates = try await agentAPI.deleteAutoScanTemplate(id: template.id).templates
            forgetAppliedTemplate(template)
        } catch let apiError as APIError where apiError.code == 404 {
            // Another client deleted it first: the result is the same.
            templates.removeAll { $0.id == template.id }
            forgetAppliedTemplate(template)
        } catch {
            templateActionError = Self.detail(of: error) ?? Self.httpFallback(error, "Couldn't delete the template.")
        }
    }

    private func forgetAppliedTemplate(_ template: AutoScanTemplateDto) {
        guard appliedTemplateName == template.name else { return }
        appliedTemplateName = nil
        templateNotice = nil
    }

    /// "GitHub · Codex": where a template looks and which agent it runs.
    static func templateSummary(_ template: AutoScanTemplateDto) -> String {
        var parts: [String] = []
        if let source = template.settings.source { parts.append(AutoScanDisplay.sourceName(source)) }
        if let cli = template.settings.cli { parts.append(AutoScanDisplay.agentName(cli)) }
        return parts.joined(separator: " · ")
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
            return "The instructions can have at most 32,000 characters."
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
            if githubLabels.isEmpty { return "Choose at least one label to pick up." }
            let assignee = githubAssignee.trimmingCharacters(in: .whitespacesAndNewlines)
            if assignee.unicodeScalars.count > Self.maxGitHubLabelLength {
                return "The assignee can have at most \(Self.maxGitHubLabelLength) characters."
            }
            let active = githubActiveLabel.trimmingCharacters(in: .whitespacesAndNewlines)
            if !active.isEmpty, let error = Self.githubLabelError(active) {
                return "Label to add: \(error)"
            }
            if !active.isEmpty, githubLabels.contains(where: { $0.lowercased() == active.lowercased() }) {
                return "The label to add must not be one of the labels to pick up."
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
        if cli == "antigravity", let error = CreateSessionModel.addDirsError(antigravityAddDirsDraft) {
            return "Additional directories: \(error)."
        }
        return nil
    }

    /// The start body: every choice, the chosen source's filter only and the
    /// chosen agent's options only (Pi has none).
    func startRequest(
        workspaceName: String,
        claudeOptions: ClaudeOptionsModel,
        initialPrompt: String
    ) -> StartScanRequest? {
        guard let source, let cli else { return nil }
        return StartScanRequest(
            workspace_name: workspaceName,
            claude_options: cli == "claude" ? claudeOptions : nil,
            codex_options: cli == "codex" ? codexOptions : nil,
            antigravity_options: cli == "antigravity" ? antigravityOptionsForStart : nil,
            opencode_options: cli == "opencode" ? opencodeOptions : nil,
            copilot_options: cli == "copilot" ? copilotOptions : nil,
            cursor_options: cli == "cursor" ? cursorOptions : nil,
            junie_options: cli == "junie" ? junieOptions : nil,
            ui: effectiveSessionView.rawValue,
            interval_minutes: intervalMinutes,
            source: source.rawValue,
            cli: cli,
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

    private static func httpFallback(_ error: Error, _ message: String) -> String {
        guard let apiError = error as? APIError else { return message }
        return "\(message) (HTTP \(apiError.code))"
    }
}
