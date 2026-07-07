import Foundation
import Observation

/// Create-session wizard state (Android `CreateSessionViewModel`). Builds
/// byte-identical `CreateSessionRequest`s: managed CLIs carry worktree/branch
/// inside their options block, shell carries them on the top-level request, and
/// custom paths always force worktree off. AutoScan mode is deferred to iOS-07,
/// so it is omitted here.
@MainActor
@Observable
final class CreateSessionModel {
    enum Step: Equatable {
        case projectSelect
        case modePick
        case config
    }

    enum Mode: String, CaseIterable, Equatable {
        case standard
        case codex
        case antigravity
        case shell
    }

    enum ProjectSelection: Equatable {
        case none
        case stored(String)
        case customPath(String)
    }

    static let claudeModels = ["opus", "sonnet", "haiku", "fable"]
    static let codexModelsFallback = ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex-spark"]
    static let reasoningEfforts = ["minimal", "low", "medium", "high"]
    static let codexSandboxes = ["read-only", "workspace-write", "danger-full-access"]
    static let codexApprovals = ["untrusted", "on-request", "never"]

    private(set) var step: Step = .projectSelect
    private(set) var projects: [ProjectInfo] = []
    private(set) var submitting = false
    var error: String?

    var selection: ProjectSelection = .none
    var customPathDraft = ""
    private(set) var customPathError: String?
    var mode: Mode?

    private(set) var claudeAvailable: Bool?
    private(set) var codexAvailable: Bool?
    private(set) var antigravityAvailable: Bool?
    private(set) var codexModels: [String] = codexModelsFallback
    private(set) var antigravityModels: [String] = []

    // Claude / Standard
    var claudeModel: String?
    var claudeEffort = "high"
    var claudeFastMode = false
    var claudeUltracode = false
    var claudeAppendSystemPrompt = ""
    var skipPermissions = true
    var noMemory = false

    // Codex
    var codexModel: String?
    var codexSandbox = "workspace-write"
    var codexApproval = "on-request"
    var codexFullAuto = false
    var codexReasoningEffort = "high"

    // Antigravity
    var antigravityModel: String?
    var antigravitySkipPermissions = false
    var antigravitySandbox = false
    var antigravityAddDirs = ""

    // Shared git options
    var worktree = false
    var branchName = ""

    // Codex login
    private(set) var showCodexLogin = false
    var codexApiKey = ""

    private let agentAPI: AgentAPI
    private let sessionRepository: SessionRepository
    private let preferencesStore: PreferencesStore
    private let navBroadcaster = Broadcaster<String>(replay: .none)

    init(agentAPI: AgentAPI, sessionRepository: SessionRepository, preferencesStore: PreferencesStore) {
        self.agentAPI = agentAPI
        self.sessionRepository = sessionRepository
        self.preferencesStore = preferencesStore
        applyPrefill()
    }

    func navEvents() -> AsyncStream<String> { navBroadcaster.subscribe(replayLatest: false) }

    // MARK: - Availability

    var claudeEnabled: Bool { claudeAvailable != false }
    var codexEnabled: Bool { codexAvailable != false }
    var antigravityEnabled: Bool { antigravityAvailable != false }

    func isEnabled(_ mode: Mode) -> Bool {
        switch mode {
        case .standard: return claudeEnabled
        case .codex: return codexEnabled
        case .antigravity: return antigravityEnabled
        case .shell: return true
        }
    }

    func reason(for mode: Mode) -> String {
        switch mode {
        case .standard:
            return claudeEnabled ? "Configure and launch a Claude session."
                : "Claude CLI is not installed on the agent."
        case .codex:
            return codexEnabled ? "Configure and launch an OpenAI Codex session."
                : "Codex CLI is not installed on the agent."
        case .antigravity:
            return antigravityEnabled ? "Configure and launch a Google Antigravity session."
                : "Antigravity CLI (agy) is not installed on the agent."
        case .shell:
            return "Bash / cmd in the chosen directory"
        }
    }

    func claudeEfforts(for model: String?) -> [String] {
        (model == "opus" || model == "fable")
            ? ["low", "medium", "high", "xhigh", "max"]
            : ["low", "medium", "high"]
    }

    // MARK: - Loading

    func load() async {
        do {
            let projects = try await agentAPI.getProjects()
            self.projects = projects
            applyProjectPrefill()
        } catch {
            // Non-fatal: the wizard still allows a custom path.
        }
    }

    func refreshCliAvailability() async {
        guard let version = try? await agentAPI.getAgentVersion() else { return }
        claudeAvailable = version.cliAvailable("claude")
        codexAvailable = version.cliAvailable("codex")
        antigravityAvailable = version.cliAvailable("antigravity")
        if let models = version.cliModels("codex"), !models.isEmpty { codexModels = models }
        if let models = version.cliModels("antigravity"), !models.isEmpty { antigravityModels = models }
    }

    // MARK: - Navigation between steps

    var canContinueProject: Bool {
        switch selection {
        case .stored: return true
        case .customPath: return !customPathDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && customPathError == nil
        case .none: return false
        }
    }

    func selectStoredProject(_ name: String) {
        selection = .stored(name)
        customPathError = nil
    }

    func setCustomPath(_ path: String) {
        customPathDraft = path
        customPathError = Self.validateCustomPath(path)
        // Store the trimmed path (sent verbatim as `cwd`); the draft keeps the
        // raw text for the field. Whitespace validates but would create the
        // session in the wrong directory.
        selection = .customPath(path.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    func continueFromProject() {
        if case .customPath = selection {
            customPathError = Self.validateCustomPath(customPathDraft)
            guard customPathError == nil else { return }
        }
        step = .modePick
    }

    func selectMode(_ mode: Mode) {
        guard isEnabled(mode) else { return }
        self.mode = mode
        step = .config
    }

    func back() {
        switch step {
        case .config: step = .modePick
        case .modePick: step = .projectSelect
        case .projectSelect: break
        }
    }

    // Antigravity: skip_permissions and sandbox are mutually exclusive.
    func setAntigravitySkipPermissions(_ on: Bool) {
        antigravitySkipPermissions = on
        if on { antigravitySandbox = false }
    }

    func setAntigravitySandbox(_ on: Bool) {
        antigravitySandbox = on
        if on { antigravitySkipPermissions = false }
    }

    // MARK: - Submit

    var canCreate: Bool {
        selection != .none && !submitting
    }

    func create() async {
        guard let mode, canCreate else { return }
        submitting = true
        error = nil
        do {
            let request = buildRequest(mode: mode)
            let response = try await sessionRepository.createSession(request)
            persistLastUsed(mode: mode, response: response)
            submitting = false
            navBroadcaster.emit(response.session_id)
        } catch let apiError as APIError where mode == .codex && apiError.errorBody.contains("codex_auth_required") {
            submitting = false
            showCodexLogin = true
        } catch {
            submitting = false
            self.error = "Failed to create session: \(String(describing: error))"
        }
    }

    func submitCodexLogin() async {
        let key = codexApiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else { return }
        showCodexLogin = false
        codexApiKey = ""
        do {
            _ = try await agentAPI.codexLogin(apiKey: key)
            await create()
        } catch {
            self.error = "Codex login failed: \(String(describing: error))"
        }
    }

    func cancelCodexLogin() {
        showCodexLogin = false
        codexApiKey = ""
    }

    func buildRequest(mode: Mode) -> CreateSessionRequest {
        let projectName: String?
        let cwd: String?
        let isCustomPath: Bool
        switch selection {
        case .stored(let name):
            projectName = name
            cwd = nil
            isCustomPath = false
        case .customPath(let path):
            projectName = nil
            cwd = path
            isCustomPath = true
        case .none:
            projectName = nil
            cwd = nil
            isCustomPath = false
        }
        let effectiveWorktree = isCustomPath ? false : worktree
        let effectiveBranch = (isCustomPath || branchName.trimmingCharacters(in: .whitespaces).isEmpty)
            ? nil : branchName

        switch mode {
        case .standard:
            let options = ClaudeOptionsModel(
                no_memory: noMemory,
                skip_permissions: skipPermissions,
                worktree: effectiveWorktree,
                branch_name: effectiveBranch,
                model: claudeFastMode ? "opus" : claudeModel,
                effort: claudeUltracode ? "xhigh" : claudeEffort,
                fast_mode: claudeFastMode,
                ultracode: claudeUltracode,
                append_system_prompt: claudeAppendSystemPrompt.isEmpty ? nil : claudeAppendSystemPrompt
            )
            return CreateSessionRequest(cwd: cwd, project_name: projectName, claude_options: options)
        case .codex:
            let options = CodexOptionsModel(
                model: codexModel,
                sandbox: codexSandbox,
                approval: codexApproval,
                reasoning_effort: codexReasoningEffort,
                full_auto: codexFullAuto,
                worktree: effectiveWorktree,
                branch_name: effectiveBranch
            )
            return CreateSessionRequest(cwd: cwd, project_name: projectName, cli: "codex", codex_options: options)
        case .antigravity:
            let options = AntigravityOptionsModel(
                model: antigravityModel,
                skip_permissions: antigravitySkipPermissions,
                sandbox: antigravitySandbox,
                add_dirs: Self.parseAddDirs(antigravityAddDirs),
                worktree: effectiveWorktree,
                branch_name: effectiveBranch
            )
            return CreateSessionRequest(cwd: cwd, project_name: projectName, cli: "antigravity", antigravity_options: options)
        case .shell:
            return CreateSessionRequest(
                cwd: cwd,
                project_name: projectName,
                worktree: effectiveWorktree,
                branch_name: effectiveBranch
            )
        }
    }

    private func persistLastUsed(mode: Mode, response: SessionResponse) {
        if case .stored(let name) = selection {
            preferencesStore.setLastUsedProjectName(name)
        }
        preferencesStore.setLastUsedSessionId(response.session_id)
        switch mode {
        case .standard:
            preferencesStore.setLastUsedClaudeOptions(ClaudeLaunchPrefs(
                model: claudeFastMode ? "opus" : claudeModel,
                effort: claudeUltracode ? "xhigh" : claudeEffort,
                fastMode: claudeFastMode,
                ultracode: claudeUltracode,
                appendSystemPrompt: claudeAppendSystemPrompt
            ))
        case .codex:
            preferencesStore.setLastUsedCodexOptions(CodexLaunchPrefs(
                model: codexModel,
                sandbox: codexSandbox,
                approval: codexApproval,
                fullAuto: codexFullAuto,
                reasoningEffort: codexReasoningEffort
            ))
        case .antigravity:
            preferencesStore.setLastUsedAntigravityOptions(AntigravityLaunchPrefs(
                model: antigravityModel,
                skipPermissions: antigravitySkipPermissions,
                sandbox: antigravitySandbox
            ))
        case .shell:
            break
        }
    }

    // MARK: - Prefill

    private func applyPrefill() {
        if let claude = preferencesStore.getLastUsedClaudeOptions() {
            claudeModel = claude.model
            claudeEffort = claude.effort
            claudeFastMode = claude.fastMode
            claudeUltracode = claude.ultracode
            claudeAppendSystemPrompt = claude.appendSystemPrompt
        }
        if let codex = preferencesStore.getLastUsedCodexOptions() {
            codexModel = codex.model
            codexSandbox = codex.sandbox
            codexApproval = codex.approval
            codexFullAuto = codex.fullAuto
            codexReasoningEffort = codex.reasoningEffort
        }
        if let antigravity = preferencesStore.getLastUsedAntigravityOptions() {
            antigravityModel = antigravity.model
            antigravitySkipPermissions = antigravity.skipPermissions
            antigravitySandbox = antigravity.sandbox && !antigravity.skipPermissions
        }
    }

    private func applyProjectPrefill() {
        guard case .none = selection,
              let lastName = preferencesStore.getLastUsedProjectName(),
              projects.contains(where: { $0.name == lastName })
        else { return }
        selection = .stored(lastName)
    }

    var selectedProject: ProjectInfo? {
        guard case .stored(let name) = selection else { return nil }
        return projects.first { $0.name == name }
    }

    /// Worktree/branch fields apply only to a stored git project.
    var showsGitOptions: Bool {
        selectedProject?.is_git == true
    }

    static func parseAddDirs(_ input: String) -> [String] {
        input
            .split(whereSeparator: { $0 == "\n" || $0 == "," })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    static func validateCustomPath(_ input: String) -> String? {
        let trimmed = input.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return "Path is required" }
        let isPosixAbsolute = trimmed.hasPrefix("/")
        let isWindowsAbsolute = trimmed.range(of: "^[A-Za-z]:[\\\\/]", options: .regularExpression) != nil
            || trimmed.hasPrefix("\\\\")
        if !(isPosixAbsolute || isWindowsAbsolute) { return "Path must be absolute" }
        return nil
    }
}
