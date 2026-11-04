import Foundation
import Observation

/// Create-session wizard state (Android `CreateSessionViewModel`). Builds
/// byte-identical `CreateSessionRequest`s: managed CLIs carry worktree/branch
/// inside their options block, shell and custom agent types carry them on the
/// top-level request, and custom paths always force worktree off.
///
/// Auto Scan is the one mode that does not create a session: it POSTs
/// `/api/autoscan/start` through `AutoScanRepository` and navigates to the
/// live monitor, so it has its own submit path (`startAutoScan`), its own
/// option bucket, and its own nav stream.
@MainActor
@Observable
final class CreateSessionModel {
    enum Step: Equatable {
        case projectSelect
        case modePick
        case config
    }

    enum Mode: Hashable, Equatable {
        case standard
        case codex
        case antigravity
        case autoScan
        case shell
        /// An account-scoped custom agent type, identified by its 12-hex id
        /// (the wire kind is `custom_<id>`). Holding only the id keeps the
        /// selection valid across renames and lets a remotely-deleted type
        /// surface the relay 404 at launch instead of silently falling back
        /// to a plain Terminal (a known Android bug, deliberately not
        /// mirrored).
        case custom(String)

        /// The static mode cards, in default order; custom-type cards follow.
        static let builtIn: [Mode] = [.standard, .codex, .antigravity, .autoScan, .shell]

        /// The id `/api/session-mode-order` stores for this card.
        var orderId: String {
            switch self {
            case .standard: return "claude"
            case .codex: return "codex"
            case .antigravity: return "antigravity"
            case .autoScan: return "auto_scan"
            case .shell: return "shell"
            case .custom(let id): return "custom_\(id)"
            }
        }

        var isCustom: Bool {
            if case .custom = self { return true }
            return false
        }
    }

    /// Editor target for the custom-agent-type sheet — `create` or
    /// `edit(existing)` (same shape as `TemplateEditorTarget`).
    enum CustomTypeEditorTarget: Identifiable, Equatable {
        case create
        case edit(CustomAgentTypeDto)

        var id: String {
            switch self {
            case .create: return "__create__"
            case .edit(let type): return type.id
            }
        }

        var type: CustomAgentTypeDto? {
            if case .edit(let type) = self { return type }
            return nil
        }
    }

    enum ProjectSelection: Equatable {
        case none
        case stored(String)
        case customPath(String)
    }

    static let claudeModels = ["opus", "sonnet", "haiku", "fable"]
    static let codexModelsFallback = [
        "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
        "gpt-5.5", "gpt-5.4", "gpt-5.4-mini",
    ]
    /// Mirror of Android `ANTIGRAVITY_MODELS` — used until the `agy` capability
    /// probe supplies a live list, so model selection still works when the probe
    /// is unavailable ("Auto"/nil is always offered alongside these).
    static let antigravityModelsFallback = [
        "Gemini 3.5 Flash (Medium)", "Gemini 3.5 Flash (High)", "Gemini 3.5 Flash (Low)",
        "Gemini 3.1 Pro (Low)", "Gemini 3.1 Pro (High)",
        "Claude Sonnet 4.6 (Thinking)", "Claude Opus 4.6 (Thinking)", "GPT-OSS 120B (Medium)",
    ]
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
    /// Ad-hoc launch directories previously used against this agent,
    /// most-recent-first. Device-local and never sent on the wire.
    private(set) var recentCustomPaths: [String] = []
    var mode: Mode?

    // Custom agent types (proto-v7)
    private(set) var customTypes: [CustomAgentTypeDto] = []
    /// The account's mode-picker order (mode ids); empty = default order.
    private(set) var modeOrder: [String] = []
    /// The project order a save in flight carries, so a late answer to an
    /// earlier save cannot overwrite a newer drop.
    private var pendingProjectOrder: [String]?
    private var projectOrderSaveTail: Task<Void, Never>?
    var typeEditorTarget: CustomTypeEditorTarget? {
        didSet { if typeEditorTarget != oldValue { typeEditorError = nil } }
    }
    private(set) var isSavingType = false
    var typeEditorError: String?
    var typeDeleteTarget: CustomAgentTypeDto?
    /// Ids of types whose DELETE is in flight, keyed per target (the
    /// `savesInFlight` pattern): the confirmation alert auto-dismisses on tap,
    /// so a second type can be confirmed while the first DELETE still awaits —
    /// a single boolean guard would silently drop that confirmed destructive
    /// action. A repeat confirm of the same type stays a no-op.
    private(set) var deletesInFlightTypeIds: Set<String> = []

    private(set) var claudeAvailable: Bool?
    private(set) var codexAvailable: Bool?
    private(set) var antigravityAvailable: Bool?
    private(set) var codexModels: [String] = codexModelsFallback
    private(set) var antigravityModels: [String] = antigravityModelsFallback
    /// Per-model Codex effort sets from `capabilities.cli.codex.model_details`;
    /// nil until an agent advertises them, so `codexEfforts(for:)` falls back
    /// to the static `reasoningEfforts` list.
    private(set) var codexModelDetails: [CliModelDetail]?
    /// Remaining `models_pending` re-probes. A budget rather than a one-shot:
    /// a legitimately slow agent-side warmup (each probe attempt may take 10s,
    /// and the old-CLI path takes two) can outlive several client probes, and
    /// a terminal pending answer would strand the visit on fallback lists with
    /// no reconciliation. Refilled by any non-pending answer; exhausted, it
    /// stops — a truly wedged warmup must not become an endless poll loop.
    private var warmupRetriesLeft = CreateSessionModel.warmupMaxRetries
    static let warmupMaxRetries = 8

    // Claude / Standard
    var claudeModel: String?
    var claudeEffort = "high"
    var claudeFastMode = false
    var claudeUltracode = false
    var claudeDebug = false
    var focusMode = true
    var claudeAppendSystemPrompt = ""
    var skipPermissions = false
    var noMemory = false

    // Codex
    var codexModel: String?
    var codexSandbox = "workspace-write"
    var codexApproval = "on-request"
    var codexFullAuto = false
    var codexReasoningEffort = "high"

    // Auto Scan — its own option bucket, deliberately not shared with the
    // Claude Code fields above so the two modes remember separate choices.
    var autoScanClaudeModel: String?
    var autoScanClaudeEffort = "high"
    var autoScanClaudeFastMode = false
    var autoScanClaudeUltracode = false
    var autoScanClaudeDebug = false
    var autoScanFocusMode = true
    var autoScanClaudeAppendSystemPrompt = ""
    var autoScanPlanMode = false
    /// Defaults ON: a scan runs unattended, so pausing at a permission prompt
    /// would strand it (Android `autoScanSkipPermissions = true`).
    var autoScanSkipPermissions = true
    var autoScanNoMemory = false
    var autoScanInitialPrompt = ""
    private(set) var autoScanStarting = false
    private(set) var activeScans: [AutoScanInstanceDto] = []
    /// Stored-project name the initial prompt was last seeded for, so
    /// re-entering the step keeps in-flight edits and only a project change
    /// re-seeds (Android `autoScanConfigInitializedFor`).
    private var autoScanPromptSeededFor: String?

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
    private let tokenStore: TokenStore
    private let customAgentTypesRepository: CustomAgentTypesRepository
    private let autoScanRepository: AutoScanRepository
    /// The agent's live project list (`projects.changed`), so an order saved
    /// on another device shows while the wizard is open. Optional so a model
    /// built without it keeps the one-shot `load()` snapshot.
    private let projectsRepository: ProjectsRepository?
    private let navBroadcaster = Broadcaster<String>(replay: .none)
    /// Carries a project name to the Auto Scan monitor. Separate from
    /// `navBroadcaster` (which carries a session id) because the two lead to
    /// different routes. `.latestClearable` because the Projects-shortcut
    /// preset emits from inside `load()`, which can run before the screen's
    /// nav task subscribes — a non-replaying stream would drop it (the
    /// `SessionListModel` auto-resume pattern); the screen clears the cache
    /// once it has navigated.
    private let autoScanNavBroadcaster = Broadcaster<String>(replay: .latestClearable)
    /// Stored project the wizard should open directly in Auto Scan config
    /// (the Projects screen shortcut); applied once `load()` resolves it.
    private let autoScanProjectPreset: String?
    private var didApplyAutoScanPreset = false

    /// Projects created inline via the New Session "Create new project…" flow,
    /// retained so a late initial `load()` (a still-pending `getProjects`) cannot
    /// clobber the just-created selection — and its Git options — with a snapshot
    /// that predates the create.
    private var locallyCreatedProjects: [ProjectInfo] = []

    init(
        agentAPI: AgentAPI,
        sessionRepository: SessionRepository,
        preferencesStore: PreferencesStore,
        tokenStore: TokenStore,
        customAgentTypesRepository: CustomAgentTypesRepository,
        autoScanRepository: AutoScanRepository,
        projectsRepository: ProjectsRepository? = nil,
        autoScanProject: String? = nil
    ) {
        self.agentAPI = agentAPI
        self.sessionRepository = sessionRepository
        self.preferencesStore = preferencesStore
        self.tokenStore = tokenStore
        self.customAgentTypesRepository = customAgentTypesRepository
        self.autoScanRepository = autoScanRepository
        self.projectsRepository = projectsRepository
        self.autoScanProjectPreset = autoScanProject
        self.customTypes = customAgentTypesRepository.currentTypes
        self.modeOrder = customAgentTypesRepository.currentModeOrder
        self.activeScans = autoScanRepository.currentScans
        applyPrefill()
    }

    func navEvents() -> AsyncStream<String> { navBroadcaster.subscribe(replayLatest: false) }

    func autoScanNavEvents() -> AsyncStream<String> { autoScanNavBroadcaster.subscribe() }

    /// Called by the screen once it has consumed the project name and
    /// navigated, so the cached id is not replayed.
    func clearAutoScanNav() { autoScanNavBroadcaster.clearReplay() }

    // MARK: - Availability

    var claudeEnabled: Bool { claudeAvailable != false }
    var codexEnabled: Bool { codexAvailable != false }
    var antigravityEnabled: Bool { antigravityAvailable != false }

    /// Auto Scan additionally needs a Linear API key on the selected stored
    /// project — the agent scans Linear for that project's issues. A custom
    /// path or no selection can never satisfy it (Android `autoScanEnabled`).
    var autoScanEnabled: Bool {
        claudeEnabled && selectedProject?.has_linear_key == true
    }

    /// The scan already running for the selected stored project, if any. Auto
    /// Scan is one-per-project, so this turns a guaranteed 409 into a route to
    /// the live monitor.
    var runningScan: AutoScanInstanceDto? {
        guard case .stored(let name) = selection else { return nil }
        return activeScans.first { $0.project_name == name }
    }

    func isEnabled(_ mode: Mode) -> Bool {
        switch mode {
        case .standard: return claudeEnabled
        case .codex: return codexEnabled
        case .antigravity: return antigravityEnabled
        // A running scan keeps the card tappable even when the launch gate
        // would refuse — tapping opens its monitor rather than the config step.
        case .autoScan: return autoScanEnabled || runningScan != nil
        case .shell: return true
        // No CLI probe for custom types — the agent runs whatever command the
        // relay resolves; a launch failure surfaces from the server instead.
        case .custom: return true
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
        case .autoScan:
            if let running = runningScan {
                return "Already running for \(running.project_name) — tap to open the live monitor."
            }
            if !claudeEnabled {
                return "Auto Scan needs the Claude CLI, which isn't installed on the agent."
            }
            if !autoScanEnabled {
                return "Auto Scan needs a Linear API key for this project."
            }
            return "Scan Linear for matching issues and launch sessions."
        case .shell:
            return "Bash / cmd in the chosen directory"
        case .custom(let id):
            // The mode card renders the command itself (monospaced); this is
            // the config-step subtitle fallback.
            return customType(id: id)?.command ?? "Runs your saved launch command"
        }
    }

    func customType(id: String) -> CustomAgentTypeDto? {
        customTypes.first { $0.id == id }
    }

    /// Config-step section title (Android `TerminalConfig` TopAppBar parity):
    /// the custom type's live name, falling back to "Terminal session" when
    /// the type was deleted remotely mid-wizard.
    func modeTitle(_ mode: Mode) -> String {
        switch mode {
        case .standard: return "Claude Code"
        case .codex: return "Codex"
        case .antigravity: return "Antigravity"
        case .autoScan: return "Auto Scan"
        case .shell: return "Terminal"
        case .custom(let id): return customType(id: id)?.name ?? "Terminal session"
        }
    }

    func claudeEfforts(for model: String?) -> [String] {
        (model == "opus" || model == "fable" || model == "sonnet")
            ? ["low", "medium", "high", "xhigh", "max"]
            : ["low", "medium", "high"]
    }

    /// The Codex model picker's options, with the keep-selected repair: a
    /// restored model absent from the advertised/fallback list (e.g. a
    /// pre-refresh `gpt-5.3-codex-spark` from persisted prefs) must stay in
    /// the options, or the SwiftUI Picker — which renders selection by tag —
    /// shows no selection while `buildRequest()` still submits the hidden
    /// value. Mirrors the effort picker's append and web's `modelOptions`.
    var codexModelOptions: [String] {
        guard let selected = codexModel, !codexModels.contains(selected) else {
            return codexModels
        }
        return codexModels + [selected]
    }

    /// The effort options for a Codex model: the agent-advertised per-model set
    /// when `model_details` knows it, else the static `reasoningEfforts`
    /// fallback. The Default (nil) model always takes the fallback — which
    /// model the agent resolves it to is unknown here (`claudeEfforts(for:)`'s
    /// nil posture).
    func codexEfforts(for model: String?) -> [String] {
        guard let model,
              let detail = codexModelDetails?.first(where: { $0.id == model })
        else { return CreateSessionModel.reasoningEfforts }
        return detail.efforts
    }

    /// Model switches route through here (not a bare assignment) so a stranded
    /// effort re-defaults: when the new model's advertised set lacks the current
    /// effort, take the catalog default, else `high` when advertised, else the
    /// set's first entry. A model without a catalog entry (or the nil Default
    /// model) keeps the effort — its capabilities are unknown, never downshift
    /// on a guess. Unlike Claude there is deliberately NO submit-time clamp:
    /// combined with the picker's keep-selected repair it would silently send a
    /// different effort than the field displays.
    func setCodexModel(_ model: String?) {
        codexModel = model
        guard let model,
              let detail = codexModelDetails?.first(where: { $0.id == model }),
              !detail.efforts.contains(codexReasoningEffort)
        else { return }
        codexReasoningEffort = detail.defaultEffort
            ?? (detail.efforts.contains("high") ? "high" : detail.efforts[0])
    }

    var effectiveClaudeModel: String? { claudeFastMode ? "opus" : claudeModel }

    /// Ultracode always forces `xhigh` (Android parity). Otherwise the
    /// user-selected effort is clamped to the set valid for the effective model,
    /// so switching to haiku (the only capped model, offering just `low`/`medium`/
    /// `high`) never submits an unsupported effort. `high` is valid for every model.
    var effectiveClaudeEffort: String {
        if claudeUltracode { return "xhigh" }
        return claudeEfforts(for: effectiveClaudeModel).contains(claudeEffort) ? claudeEffort : "high"
    }

    /// Same fast-mode / ultracode rules as the Claude Code bucket, applied to
    /// Auto Scan's own selections.
    var effectiveAutoScanModel: String? { autoScanClaudeFastMode ? "opus" : autoScanClaudeModel }

    var effectiveAutoScanEffort: String {
        if autoScanClaudeUltracode { return "xhigh" }
        return claudeEfforts(for: effectiveAutoScanModel).contains(autoScanClaudeEffort)
            ? autoScanClaudeEffort : "high"
    }

    // MARK: - Loading

    func load() async {
        do {
            var fetched = try await agentAPI.getProjects()
            // A create can land while this initial fetch is still in flight;
            // re-add any inline-created project the snapshot predates so it is
            // not dropped (the fetched entry wins when both are present).
            for created in locallyCreatedProjects where !fetched.contains(where: { $0.name == created.name }) {
                fetched.append(created)
            }
            self.projects = fetched
            applyProjectPrefill()
            if autoScanProjectPreset != nil {
                // The preset chooses between the config step and the live
                // monitor from `runningScan`, and it applies exactly once — so
                // the scan list has to be warm *before* it runs. `init` seeds
                // it from a repository cache that is empty on a cold start, and
                // the sibling `observeScans()` task refreshes concurrently with
                // no ordering guarantee. Without this the shortcut lands in the
                // config step for an already-scanning project, recoverable only
                // by a start that 409s — and not even that when the Claude CLI
                // is missing, since the Start button is then disabled.
                activeScans = await autoScanRepository.refresh().scans
            }
            // The preset skips the mode picker, where the probe normally runs,
            // so the config step would not know whether Claude is installed.
            if applyAutoScanPreset() {
                await refreshCliAvailability()
            }
        } catch {
            // Non-fatal: the wizard still allows a custom path. The repository's
            // cached list is not a fallback — it is not scoped to this account
            // or agent.
        }
    }

    /// Seeds the scan list from REST (autoscan may be unconfigured — swallowed
    /// by `refresh`), then mirrors bus-driven updates so the mode card can
    /// report a scan started from another device mid-wizard.
    func observeScans() async {
        _ = await autoScanRepository.refresh()
        for await scans in autoScanRepository.scans() {
            activeScans = scans
            // The agent-wide default prompt rides in on the same payload, from
            // the REST seed above *and* from every `autoscan.list.changed`
            // push — so re-seed here rather than only after the refresh, which
            // silently returns the unchanged cache when the agent is briefly
            // unreachable. Idempotent: the empty check keeps an in-flight edit
            // untouched, and `seedAutoScanPrompt` is a no-op once latched.
            if autoScanInitialPrompt.isEmpty { seedAutoScanPrompt() }
        }
    }

    /// Projects-screen shortcut: drop straight into the Auto Scan config step
    /// for `autoScanProjectPreset`. Runs once, after `load()` has the project
    /// (its `has_linear_key` is the launch gate). An already-running scan
    /// routes to the monitor instead; a project that lost its Linear key falls
    /// back to the mode picker rather than a dead end.
    /// Returns whether the config step was entered.
    @discardableResult
    private func applyAutoScanPreset() -> Bool {
        guard !didApplyAutoScanPreset, let name = autoScanProjectPreset else { return false }
        guard projects.contains(where: { $0.name == name }) else { return false }
        didApplyAutoScanPreset = true
        selection = .stored(name)
        if let running = runningScan {
            autoScanNavBroadcaster.emit(running.project_name)
            return false
        }
        guard selectedProject?.has_linear_key == true else {
            step = .modePick
            return false
        }
        seedAutoScanPrompt()
        mode = .autoScan
        step = .config
        return true
    }

    func refreshCliAvailability() async {
        guard let version = try? await agentAPI.getAgentVersion() else { return }
        claudeAvailable = version.cliAvailable("claude")
        codexAvailable = version.cliAvailable("codex")
        antigravityAvailable = version.cliAvailable("antigravity")
        if let models = version.cliModels("codex"), !models.isEmpty { codexModels = models }
        if let models = version.cliModels("antigravity"), !models.isEmpty { antigravityModels = models }
        // Overwritten, never keep-last-known (unlike the model lists above): a
        // stale catalog retained from a previous probe — the agent restarted
        // on an older build, say — would keep offering efforts the current
        // agent may reject.
        let details = version.cliModelDetails("codex")
        codexModelDetails = details
        // models_pending: the agent's startup enumeration was still warming
        // when it answered, so absent details are provisional — no reconcile
        // (the marker itself proves a catalog-era agent, so a persisted
        // catalog effort is safe to keep), and delayed re-probes continue
        // until the warmed answer arrives, bounded by `warmupRetriesLeft`.
        if version.cliModelsPending("codex") {
            if warmupRetriesLeft > 0 {
                warmupRetriesLeft -= 1
                Task { [weak self] in
                    try? await Task.sleep(nanoseconds: 2_500_000_000)
                    await self?.refreshCliAvailability()
                }
            }
            return
        }
        warmupRetriesLeft = CreateSessionModel.warmupMaxRetries
        codexReasoningEffort = reconciledCodexEffort(
            model: codexModel, effort: codexReasoningEffort, details: details
        )
    }

    /// The reasoning effort actually submittable to the probed agent. No
    /// `model_details` advertised → possibly a pre-catalog agent whose wire
    /// schema still 422s efforts outside the static vocabulary, so anything
    /// outside it drops to the default. Details advertised and the selected
    /// model's entry omits the effort → the catalog says the model cannot
    /// serve it, so the same re-default `setCodexModel` applies on a switch
    /// runs here. A model without a catalog entry (or the nil Default model)
    /// keeps the effort — its capabilities are unknown, never downshift on a
    /// guess.
    private func reconciledCodexEffort(
        model: String?, effort: String, details: [CliModelDetail]?
    ) -> String {
        guard let details else {
            return CreateSessionModel.reasoningEfforts.contains(effort) ? effort : "high"
        }
        guard let model,
              let detail = details.first(where: { $0.id == model }),
              !detail.efforts.contains(effort)
        else { return effort }
        return detail.defaultEffort
            ?? (detail.efforts.contains("high") ? "high" : detail.efforts[0])
    }

    /// Mirrors the repository's push-driven cache into the wizard. The loop
    /// only reassigns `customTypes` — a selected `.custom` mode is deliberately
    /// kept when its type disappears (see `Mode.custom`).
    func observeCustomTypes() async {
        for await types in customAgentTypesRepository.types() {
            customTypes = types
        }
    }

    /// Best-effort cold-start seed (the bus reconcile owns steady-state).
    func refreshCustomTypes() async {
        try? await customAgentTypesRepository.refreshCustomTypes()
    }

    // MARK: - Reorder

    func observeModeOrder() async {
        for await order in customAgentTypesRepository.modeOrder() {
            modeOrder = order
        }
    }

    /// Best-effort cold-start seed of the mode order.
    func refreshModeOrder() async {
        try? await customAgentTypesRepository.refreshModeOrder()
    }

    /// The mode-picker cards, built-in and custom, in the account's order.
    var orderedModes: [Mode] {
        Self.orderModes(Mode.builtIn + customTypes.map { .custom($0.id) }, by: modeOrder)
    }

    /// Put `modes` in the saved `order`. A mode the order names is ranked by
    /// its place there; a mode it does not name (a custom type created after
    /// the last reorder, a mode a newer client added) keeps its default place
    /// among the other unnamed modes, after the named ones. Same rule on
    /// Android and web.
    static func orderModes(_ modes: [Mode], by order: [String]) -> [Mode] {
        guard !order.isEmpty else { return modes }
        var rank: [String: Int] = [:]
        for (index, id) in order.enumerated() where rank[id] == nil {
            rank[id] = index
        }
        return modes.enumerated()
            .map { (key: rank[$0.element.orderId] ?? order.count + $0.offset, mode: $0.element) }
            .sorted { $0.key < $1.key }
            .map(\.mode)
    }

    /// Save the account's mode order after a drag; the repository shows the
    /// new order at once and reverts it if the save fails.
    func moveModes(fromOffsets source: IndexSet, toOffset destination: Int) {
        var ids = orderedModes.map(\.orderId)
        let before = ids
        ids.move(fromOffsets: source, toOffset: destination)
        guard ids != before else { return }
        Task { [weak self, customAgentTypesRepository] in
            do {
                try await customAgentTypesRepository.saveModeOrder(ids)
            } catch {
                self?.error = "Reorder failed — reverted."
            }
        }
    }

    /// Save the agent's project order after a drag (every client shows it).
    /// Saves run one at a time, in the order the user made them.
    func moveProjects(fromOffsets source: IndexSet, toOffset destination: Int) {
        let before = projects.map(\.name)
        projects.move(fromOffsets: source, toOffset: destination)
        let names = projects.map(\.name)
        guard names != before else { return }
        pendingProjectOrder = names
        let previous = projectOrderSaveTail
        let agentAPI = self.agentAPI
        let save = Task { @MainActor [weak self] in
            await previous?.value
            do {
                let saved = try await agentAPI.reorderProjects(ProjectOrderRequest(names: names))
                self?.projectOrderSaved(names, saved)
            } catch {
                self?.projectOrderFailed(names)
            }
        }
        projectOrderSaveTail = Task { await save.value }
    }

    private func projectOrderSaved(_ names: [String], _ saved: [ProjectInfo]) {
        // Take the confirmed `order` values even while a newer drop is
        // pending, so a later failure reverts to what the agent holds.
        let confirmed = Dictionary(saved.map { ($0.name, $0) }, uniquingKeysWith: { first, _ in first })
        projects = projects.map { confirmed[$0.name] ?? $0 }
        guard pendingProjectOrder == names else { return }
        pendingProjectOrder = nil
        applyServerProjects(saved)
    }

    /// Mirrors the repository's push-driven list into the wizard. The value the
    /// stream replays on subscribe is skipped: it can predate this agent (the
    /// repository is not purged on sign-out), and `load()` fetches the list
    /// itself. Every push after it is applied. The repository seeds its stream
    /// with `[]`, so the first element is always that replay.
    func observeProjects() async {
        guard let projectsRepository else { return }
        var isReplay = true
        for await list in projectsRepository.projects() {
            defer { isReplay = false }
            if isReplay { continue }
            applyServerProjects(list)
        }
    }

    /// Show a server list: projects created inline are kept, and a drop whose
    /// save is still in flight keeps its order until the server matches it.
    private func applyServerProjects(_ list: [ProjectInfo]) {
        var merged = list
        for created in locallyCreatedProjects where !merged.contains(where: { $0.name == created.name }) {
            merged.append(created)
        }
        merged.sort { $0.order < $1.order }
        if let pending = pendingProjectOrder {
            if merged.map(\.name) == pending {
                pendingProjectOrder = nil
            } else {
                let rank = Dictionary(pending.enumerated().map { ($1, $0) }, uniquingKeysWith: { first, _ in first })
                merged.sort { (rank[$0.name] ?? Int.max) < (rank[$1.name] ?? Int.max) }
            }
        }
        projects = merged
    }

    /// A local move never touches `order`, so sorting by it restores the
    /// server's order.
    private func projectOrderFailed(_ names: [String]) {
        guard pendingProjectOrder == names else { return }
        pendingProjectOrder = nil
        projects.sort { $0.order < $1.order }
        error = "Reorder failed — reverted."
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

    /// Called after `ProjectFormSheet` reports a successful create (New Session
    /// inline "Create new project…"). Mirrors Android `onProjectCreated`: selects
    /// the returned project and gates worktree/branch off its own `is_git`. The
    /// project is merged into this wizard's `projects` at once, so
    /// `selectedProject`/`showsGitOptions` resolve before the `projects.changed`
    /// echo reaches `observeProjects()`.
    func onProjectCreated(_ project: ProjectInfo) {
        Self.upsert(project, into: &projects)
        Self.upsert(project, into: &locallyCreatedProjects)
        selection = .stored(project.name)
        customPathDraft = ""
        customPathError = nil
        if !project.is_git {
            worktree = false
            branchName = ""
        }
    }

    private static func upsert(_ project: ProjectInfo, into list: inout [ProjectInfo]) {
        if let index = list.firstIndex(where: { $0.name == project.name }) {
            list[index] = project
        } else {
            list.append(project)
        }
    }

    /// Enters "Custom path…" mode and loads the per-agent recents. Re-selecting
    /// while already in the mode only refreshes them: the row stays on screen
    /// (as on Android), so a stray second tap must not wipe a draft the user
    /// has already typed.
    func selectCustomPath() {
        if case .customPath = selection {
            refreshRecentCustomPaths()
            return
        }
        selection = .customPath("")
        customPathDraft = ""
        customPathError = nil
        worktree = false
        branchName = ""
        refreshRecentCustomPaths()
    }

    /// Fills the field from a recent, so a tapped row runs the same validation
    /// as typed text (Android `pickRecentCustomPath`).
    func pickRecentCustomPath(_ path: String) {
        setCustomPath(path)
    }

    func removeRecentCustomPath(_ path: String) {
        guard let agentKey = agentScopeKey else { return }
        preferencesStore.removeRecentCustomPath(agentKey: agentKey, path: path)
        refreshRecentCustomPaths()
    }

    private func refreshRecentCustomPaths() {
        guard let agentKey = agentScopeKey else {
            recentCustomPaths = []
            return
        }
        recentCustomPaths = preferencesStore.getRecentCustomPaths(agentKey: agentKey)
    }

    private var agentScopeKey: String? {
        tokenStore.getAgentScopeKey()
    }

    func setCustomPath(_ path: String) {
        customPathDraft = path
        customPathError = Self.validateCustomPath(path)
        // Store the trimmed path (sent verbatim as `cwd`); the draft keeps the
        // raw text for the field. Whitespace validates but would create the
        // session in the wrong directory, and the trailing separator a picked
        // folder suggestion leaves would duplicate a recent.
        selection = .customPath(
            PathCompletion.trimTrailingSeparator(path.trimmingCharacters(in: .whitespacesAndNewlines))
        )
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
        if mode == .autoScan {
            // One scan per project: an existing one is opened, not reconfigured.
            if let running = runningScan {
                autoScanNavBroadcaster.emit(running.project_name)
                return
            }
            seedAutoScanPrompt()
        }
        self.mode = mode
        step = .config
    }

    /// Per-project saved prompt → the agent-wide default (Android
    /// `selectAutoScanMode`). Re-seeds only when the selected project changed,
    /// so returning to the step does not discard the user's edits.
    ///
    /// Latches `autoScanPromptSeededFor` only once a source actually resolved.
    /// The agent-wide default rides in on the scan-list refresh, so reaching
    /// this step before that lands would otherwise mark the project seeded with
    /// an empty prompt and strand it that way for the rest of the wizard's
    /// life — `observeScans()` re-attempts the seed once the refresh returns.
    private func seedAutoScanPrompt() {
        guard case .stored(let name) = selection, autoScanPromptSeededFor != name else { return }
        guard let prompt = projects.first(where: { $0.name == name })?.auto_scan_prompt
            ?? autoScanRepository.autoScanPrompt
        else { return }
        autoScanPromptSeededFor = name
        autoScanInitialPrompt = prompt
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
        // Auto Scan has its own submit button and endpoint; it never reaches
        // the create-session path.
        guard let request = buildRequest(mode: mode) else { return }
        // Both halves of the recents write are pinned before the round-trip: the
        // wizard stays interactive while the POST is in flight, so re-reading
        // afterwards could file a directory that was never launched — or file it
        // under whichever agent the user switched to meanwhile.
        let launchSelection = selection
        let launchScopeKey = agentScopeKey
        submitting = true
        error = nil
        do {
            let response = try await sessionRepository.createSession(request)
            persistLastUsed(
                selection: launchSelection,
                scopeKey: launchScopeKey,
                mode: mode,
                response: response
            )
            submitting = false
            navBroadcaster.emit(response.session_id)
        } catch let apiError as APIError where mode == .codex && apiError.errorBody.contains("codex_auth_required") {
            submitting = false
            showCodexLogin = true
        } catch let apiError as APIError where Self.isAgentUpdateRequired(apiError.errorBody) {
            // Relay-side gate: the agent's live tunnel negotiated a protocol
            // too old to launch custom agent types.
            submitting = false
            self.error = "Update your agent to launch custom agent types"
        } catch let apiError as APIError where apiError.code == 404 && mode.isCustom
            && ConflictDetail.string(from: apiError.errorBody) == "Custom agent type not found" {
            // The type was deleted between rendering the card and the launch —
            // refresh so the stale card disappears. Keyed on the relay's exact
            // detail string, not the status alone: a 404 from a concurrently
            // deleted project must fall through to the generic message.
            submitting = false
            self.error = "This agent type was deleted"
            await refreshCustomTypes()
        } catch {
            submitting = false
            self.error = "Failed to create session: \(String(describing: error))"
        }
    }

    /// The relay's proto-gate 409 carries a top-level marker (unlike the codex
    /// marker, which is matched anywhere in the body).
    static func isAgentUpdateRequired(_ errorBody: String) -> Bool {
        guard
            let data = errorBody.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return false }
        return object["agent_update_required"] as? Bool == true
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

    // MARK: - Custom agent type editor / delete

    // Server-enforced limits on custom agent types (post-trim); validated
    // client-side too so the editor errors without a round trip.
    static let maxTypeNameLength = 60
    static let maxTypeCommandLength = 1000

    /// Android-parity validation on trimmed values; nil means valid. Lengths
    /// are counted in Unicode scalars to match the server's limits (Pydantic
    /// `max_length` counts code points) — Swift's `count` counts grapheme
    /// clusters and would falsely accept supplementary-plane input (emoji,
    /// rare CJK) the server rejects.
    static func validateCustomType(name: String, command: String) -> String? {
        if name.isEmpty { return "Name is required" }
        if name.unicodeScalars.count > maxTypeNameLength {
            return "Name must be at most \(maxTypeNameLength) characters"
        }
        if command.isEmpty { return "Command is required" }
        if command.unicodeScalars.count > maxTypeCommandLength {
            return "Command must be at most \(maxTypeCommandLength) characters"
        }
        return nil
    }

    /// Undo iOS smart punctuation in a launch command: curly quotes back to
    /// ASCII quotes, em-dash back to the `--` it was typed as, en-dash to `-`.
    /// Applied live by the editor *and* on save (traits don't cover paste).
    /// Deliberate iOS-only divergence — Android relies on keyboard hints.
    static func sanitizeCommandPunctuation(_ input: String) -> String {
        var result = ""
        result.reserveCapacity(input.count)
        for character in input {
            switch character {
            case "\u{201C}", "\u{201D}", "\u{201E}": result.append("\"")
            case "\u{2018}", "\u{2019}", "\u{201A}": result.append("'")
            case "\u{2014}": result.append("--")
            case "\u{2013}": result.append("-")
            default: result.append(character)
            }
        }
        return result
    }

    func startCreateType() { typeEditorTarget = .create }

    func startEditType(_ type: CustomAgentTypeDto) { typeEditorTarget = .edit(type) }

    /// Save from the editor sheet. Keeps the editor (and the user's draft)
    /// until the save succeeds; the server's 409 details (20-type cap, name
    /// conflict) surface inline via `typeEditorError`.
    func saveCustomType(name: String, command: String) async {
        guard !isSavingType, let target = typeEditorTarget else { return }
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedCommand = Self.sanitizeCommandPunctuation(command)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let validationError = Self.validateCustomType(name: trimmedName, command: trimmedCommand) {
            typeEditorError = validationError
            return
        }
        isSavingType = true
        defer { isSavingType = false }
        do {
            switch target {
            case .edit(let type):
                // Both fields always go on the wire (Android parity) — the
                // PATCH contract has no empty-body form.
                try await customAgentTypesRepository.updateType(
                    typeId: type.id,
                    name: trimmedName,
                    command: trimmedCommand
                )
            case .create:
                try await customAgentTypesRepository.createType(name: trimmedName, command: trimmedCommand)
            }
            typeEditorTarget = nil  // dismiss only on success
        } catch let apiError as APIError {
            typeEditorError = ConflictDetail.stringOrNil(from: apiError.errorBody)
                ?? "Couldn't save (HTTP \(apiError.code))"
        } catch {
            typeEditorError = "Couldn't save the agent type"
        }
    }

    /// Pessimistic delete; a 403 means the type is already gone elsewhere
    /// (repeat delete / another device) and counts as success — followed by a
    /// best-effort refresh so the cards converge either way.
    /// `typeDeleteTarget` is deliberately not touched here: the alert's
    /// presentation binding clears it on dismissal, and nilling it from this
    /// async flow could tear down a confirmation freshly raised for a
    /// different type.
    func confirmDeleteType(_ type: CustomAgentTypeDto) async {
        guard deletesInFlightTypeIds.insert(type.id).inserted else { return }
        defer { deletesInFlightTypeIds.remove(type.id) }
        do {
            try await customAgentTypesRepository.deleteType(typeId: type.id)
        } catch let apiError as APIError where apiError.code == 403 {
            await refreshCustomTypes()
        } catch {
            self.error = "Failed to delete agent type: \(String(describing: error))"
        }
    }

    // MARK: - Auto Scan submit

    var canStartAutoScan: Bool {
        guard case .stored = selection else { return false }
        return claudeEnabled && !autoScanStarting
    }

    /// `POST /api/autoscan/start`, then navigate to the live monitor. Note
    /// this does **not** create a session — the scanner spawns its own,
    /// one per matching Linear issue.
    func startAutoScan() async {
        guard case .stored(let projectName) = selection, !autoScanStarting else { return }
        autoScanStarting = true
        error = nil
        do {
            try await autoScanRepository.startScan(
                projectName: projectName,
                claudeOptions: autoScanClaudeOptions()
            )
            preferencesStore.setLastUsedProjectName(projectName)
            preferencesStore.setLastUsedAutoScanOptions(autoScanLaunchPrefs())
            autoScanStarting = false
            autoScanNavBroadcaster.emit(projectName)
        } catch AutoScanStartError.alreadyRunning(let running) {
            // Started elsewhere while the step was open — resolve it the same
            // way the mode card does, by opening the monitor.
            autoScanStarting = false
            autoScanNavBroadcaster.emit(running)
        } catch AutoScanStartError.capacity(let maxScans) {
            autoScanStarting = false
            self.error = "Auto Scan capacity reached (max \(maxScans))."
        } catch {
            autoScanStarting = false
            self.error = "Failed to start Auto Scan: \(String(describing: error))"
        }
    }

    /// `worktree` is hardcoded true: the scanner branches a worktree per Linear
    /// issue so concurrent issues stay isolated (Android `submitAutoScan`).
    func autoScanClaudeOptions() -> ClaudeOptionsModel {
        ClaudeOptionsModel(
            plan_mode: autoScanPlanMode,
            no_memory: autoScanNoMemory,
            skip_permissions: autoScanSkipPermissions,
            initial_prompt: Self.blankToNil(autoScanInitialPrompt),
            worktree: true,
            model: effectiveAutoScanModel,
            effort: effectiveAutoScanEffort,
            fast_mode: autoScanClaudeFastMode,
            ultracode: autoScanClaudeUltracode,
            append_system_prompt: Self.blankToNil(autoScanClaudeAppendSystemPrompt),
            debug: autoScanClaudeDebug,
            focus_mode: autoScanFocusMode
        )
    }

    private func autoScanLaunchPrefs() -> ClaudeLaunchPrefs {
        ClaudeLaunchPrefs(
            model: effectiveAutoScanModel,
            effort: effectiveAutoScanEffort,
            fastMode: autoScanClaudeFastMode,
            ultracode: autoScanClaudeUltracode,
            appendSystemPrompt: autoScanClaudeAppendSystemPrompt,
            skipPermissions: autoScanSkipPermissions,
            noMemory: autoScanNoMemory,
            planMode: autoScanPlanMode,
            debug: autoScanClaudeDebug,
            focusMode: autoScanFocusMode
        )
    }

    private static func blankToNil(_ value: String) -> String? {
        value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : value
    }

    /// `nil` for `.autoScan`, which starts a scan instead of creating a
    /// session — every other mode maps onto a request.
    func buildRequest(mode: Mode) -> CreateSessionRequest? {
        let projectName: String?
        let cwd: String?
        switch selection {
        case .stored(let name):
            projectName = name
            cwd = nil
        case .customPath(let path):
            projectName = nil
            cwd = path
        case .none:
            projectName = nil
            cwd = nil
        }
        // Worktree/branch apply only to a stored git project (`showsGitOptions`);
        // a non-git project or a custom path must not leak a stale toggle whose
        // UI is hidden and therefore unclearable.
        let gitEligible = showsGitOptions
        let effectiveWorktree = gitEligible ? worktree : false
        let effectiveBranch = (gitEligible && !branchName.trimmingCharacters(in: .whitespaces).isEmpty)
            ? branchName : nil

        switch mode {
        case .autoScan:
            return nil
        case .standard:
            let options = ClaudeOptionsModel(
                no_memory: noMemory,
                skip_permissions: skipPermissions,
                worktree: effectiveWorktree,
                branch_name: effectiveBranch,
                model: effectiveClaudeModel,
                effort: effectiveClaudeEffort,
                fast_mode: claudeFastMode,
                ultracode: claudeUltracode,
                append_system_prompt: Self.blankToNil(claudeAppendSystemPrompt),
                debug: claudeDebug,
                focus_mode: focusMode
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
        case .custom(let id):
            // Plain-terminal semantics plus the `cli` discriminator. Only the
            // type id goes on the wire — the relay resolves the stored
            // command server-side; the client never sends it.
            return CreateSessionRequest(
                cwd: cwd,
                project_name: projectName,
                cli: "custom_\(id)",
                worktree: effectiveWorktree,
                branch_name: effectiveBranch
            )
        }
    }

    private func persistLastUsed(
        selection: ProjectSelection,
        scopeKey: String?,
        mode: Mode,
        response: SessionResponse
    ) {
        switch selection {
        case .stored(let name):
            preferencesStore.setLastUsedProjectName(name)
        case .customPath(let path):
            // An ad-hoc directory is remembered as a per-agent recent and
            // deliberately never as the last-used project, so reopening the wizard
            // still defaults to the last real project. Re-validated because
            // `setCustomPath` assigns `selection` even when the draft is invalid.
            if let scopeKey, Self.validateCustomPath(path) == nil {
                preferencesStore.addRecentCustomPath(agentKey: scopeKey, path: path)
                refreshRecentCustomPaths()
            }
        case .none:
            break
        }
        preferencesStore.setLastUsedSessionId(response.session_id)
        switch mode {
        case .standard:
            preferencesStore.setLastUsedClaudeOptions(ClaudeLaunchPrefs(
                model: effectiveClaudeModel,
                effort: effectiveClaudeEffort,
                fastMode: claudeFastMode,
                ultracode: claudeUltracode,
                appendSystemPrompt: claudeAppendSystemPrompt,
                skipPermissions: skipPermissions,
                noMemory: noMemory,
                debug: claudeDebug,
                focusMode: focusMode
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
        // Auto Scan persists its own bucket from `startAutoScan`, which never
        // routes through `create()`.
        case .autoScan, .shell, .custom:
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
            skipPermissions = claude.skipPermissions
            noMemory = claude.noMemory
            claudeDebug = claude.debug
            focusMode = claude.focusMode
        }
        if let autoScan = preferencesStore.getLastUsedAutoScanOptions() {
            autoScanClaudeModel = autoScan.model
            autoScanClaudeEffort = autoScan.effort
            autoScanClaudeFastMode = autoScan.fastMode
            autoScanClaudeUltracode = autoScan.ultracode
            autoScanClaudeAppendSystemPrompt = autoScan.appendSystemPrompt
            autoScanSkipPermissions = autoScan.skipPermissions
            autoScanNoMemory = autoScan.noMemory
            autoScanPlanMode = autoScan.planMode
            autoScanClaudeDebug = autoScan.debug
            autoScanFocusMode = autoScan.focusMode
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
        // Scalar-level separator set (not `split(whereSeparator:)`): Swift treats
        // `\r\n` as one grapheme, so a Character comparison never matches CRLF —
        // splitting on the CharacterSet handles LF, CR, CRLF, and commas alike.
        input
            .components(separatedBy: CharacterSet(charactersIn: "\n\r,"))
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
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
