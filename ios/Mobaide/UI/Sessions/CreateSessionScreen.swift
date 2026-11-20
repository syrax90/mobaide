import SwiftUI

/// Create-session wizard (Android `CreateSessionScreen`), the real body for the
/// `createSession` route: workspace select → mode pick (unavailable reasons in
/// `warningYellow`) → per-kind config.
struct CreateSessionScreen: View {
    /// Stored workspace to open directly in Auto Scan config (the Workspaces
    /// screen's "Start Auto Scan…" shortcut); nil for the normal wizard.
    var autoScanWorkspace: String?

    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            CreateSessionContent(container: container, autoScanWorkspace: autoScanWorkspace)
        }
    }
}

private struct CreateSessionContent: View {
    static let claudeCodeModeHint =
        "Experimental. Claude writes JavaScript that makes several tool calls in one step. "
        + "Sessions open in the terminal. Claude Code asks you to confirm until you accept; "
        + "turn Code Mode off here to stop the question."
    static let opencodeCodeModeHint =
        "OpenCode 2 calls MCP tools from JavaScript, several in one step. "
        + "Turn it off to have the model call each tool directly."
    static let piCodeModeHint =
        "Adds Pi's codemode tool: the model writes JavaScript that calls several tools in one step."

    @State private var model: CreateSessionModel
    @State private var toast: ToastMessage?
    @State private var workspaceSheet: WorkspaceFormMode?
    @State private var pathSuggestions: PathSuggestionsModel
    @State private var claudeAdvancedExpanded = false
    @State private var codexAdvancedExpanded = false
    @State private var autoScanAdvancedExpanded = false
    @State private var autoScanCodexAdvancedExpanded = false
    @State private var saveTemplateShown = false
    @State private var templateToDelete: AutoScanTemplateDto?
    @State private var showContinueSession = false
    @State private var pendingContinue: ChatStoredSession?
    @State private var baseBranchPickerShown = false
    /// Reorder mode for the workspace and mode lists: rows show the native drag
    /// handles and a tap no longer picks (the Workspaces screen's Edit pattern).
    @State private var editMode: EditMode = .inactive
    @FocusState private var customPathFocused: Bool

    @Environment(Router.self) private var router
    @Environment(\.mobaideColors) private var colors
    @Environment(\.scenePhase) private var scenePhase

    private let container: AppContainer

    init(container: AppContainer, autoScanWorkspace: String?) {
        self.container = container
        _model = State(initialValue: CreateSessionModel(
            agentAPI: container.agentAPI,
            sessionRepository: container.sessionRepository,
            preferencesStore: container.preferencesStore,
            tokenStore: container.tokenStore,
            customAgentTypesRepository: container.customAgentTypesRepository,
            autoScanRepository: container.autoScanRepository,
            workspacesRepository: container.workspacesRepository,
            autoScanWorkspace: autoScanWorkspace
        ))
        _pathSuggestions = State(initialValue: PathSuggestionsModel(repository: container.pathSuggestionRepository))
    }

    var body: some View {
        @Bindable var model = model
        List {
            Group {
                switch model.step {
                case .workspaceSelect: workspaceSelectStep
                case .modePick: modePickStep
                case .config: configStep
                }
            }
            .pixelFormSection()
        }
        .pixelForm()
        .environment(\.editMode, $editMode)
        .navigationTitle("New Session")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(model.step != .workspaceSelect)
        .toolbar {
            if model.step != .workspaceSelect {
                ToolbarItem(placement: .topBarLeading) {
                    Button { model.back() } label: {
                        Label("Back", systemImage: "chevron.backward")
                    }
                }
            }
            if canReorder {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(isReordering ? "Done" : "Reorder") {
                        withAnimation { editMode = isReordering ? .inactive : .active }
                    }
                    .fontWeight(isReordering ? .semibold : .regular)
                }
            }
        }
        .toast($toast)
        .sheet(item: $workspaceSheet) { mode in
            WorkspaceFormSheet(mode: mode, container: container, onCreated: { model.onWorkspaceCreated($0) })
        }
        .task { await model.load() }
        .task { await model.observeCustomTypes() }
        .task { await model.observeModeOrder() }
        .task { await model.observeWorkspaces() }
        .task { await model.refreshModeOrder() }
        .task { await model.observeScans() }
        .task(id: model.worktreeBasesLoadKey) { await model.loadWorktreeBases() }
        // A pushed setup terminal cancels these and popping back runs them
        // again, so returning from `gh auth login` checks GitHub again.
        .task(id: model.autoScanGitHubLoadKey) {
            guard model.autoScanGitHubLoadKey != nil else { return }
            await model.autoScanSetup.refreshGitHubStatus()
        }
        .task(id: model.autoScanLinearLoadKey) {
            guard model.autoScanLinearLoadKey != nil else { return }
            await model.autoScanSetup.loadLinearMetadata()
        }
        .task(id: model.showsAutoScanTemplates) {
            guard model.showsAutoScanTemplates else { return }
            await model.autoScanSetup.loadTemplates()
        }
        .sheet(isPresented: $saveTemplateShown, onDismiss: {
            model.autoScanSetup.clearTemplateActionError()
        }) {
            AutoScanSaveTemplateSheet(setup: model.autoScanSetup) { name in
                await model.saveAutoScanTemplate(name: name)
            }
        }
        .alert(
            "Delete template “\(templateToDelete?.name ?? "")”?",
            isPresented: Binding(
                get: { templateToDelete != nil },
                set: { if !$0 { templateToDelete = nil } }
            ),
            presenting: templateToDelete
        ) { template in
            Button("Delete", role: .destructive) {
                Task { await model.autoScanSetup.deleteTemplate(template) }
            }
            Button("Cancel", role: .cancel) { templateToDelete = nil }
        } message: { _ in
            Text("Scans that are running keep their choices.")
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await model.recheckGitHub() } }
        }
        .sheet(isPresented: $baseBranchPickerShown) {
            if case .loaded(let bases) = model.worktreeBases {
                WorktreeBasePickerSheet(bases: bases, selected: model.baseBranch) { model.pickBaseBranch($0) }
            }
        }
        .onChange(of: model.step) { _, step in
            editMode = .inactive
            if step == .modePick {
                Task { await model.refreshCliAvailability() }
                Task { await model.refreshCustomTypes() }
            }
        }
        .sheet(item: $model.typeEditorTarget) { target in
            CustomAgentTypeEditorSheet(model: model, target: target.type)
        }
        // The pick runs after the sheet is gone, so the push it may cause
        // never races the dismissal.
        .sheet(isPresented: $showContinueSession, onDismiss: {
            guard let stored = pendingContinue else { return }
            pendingContinue = nil
            Task { await model.continueSession(stored) }
        }) {
            ContinueSessionSheet(
                agentAPI: container.agentAPI,
                kind: model.continueKind,
                live: model.continueIsLive,
                folder: model.continueFolder
            ) { stored in
                pendingContinue = stored
            }
        }
        .alert(
            "Delete agent type?",
            isPresented: typeDeletePresented,
            presenting: model.typeDeleteTarget
        ) { type in
            Button("Delete", role: .destructive) {
                Task { await model.confirmDeleteType(type) }
            }
            Button("Cancel", role: .cancel) { model.typeDeleteTarget = nil }
        } message: { type in
            Text("Delete \"\(type.name)\"? Saved shortcuts for this agent type will also be deleted. Running sessions are not affected.")
        }
        .onChange(of: model.error) { _, newValue in
            if let newValue {
                toast = ToastMessage(text: newValue)
                model.error = nil
            }
        }
        .alert("Codex login required", isPresented: codexLoginPresented) {
            SecureField("OpenAI API key", text: $model.codexApiKey)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
            Button("Log in") { Task { await model.submitCodexLogin() } }
            Button("Cancel", role: .cancel) { model.cancelCodexLogin() }
        } message: {
            Text("This agent has no Codex credentials. Enter an API key to continue.")
        }
        .task {
            for await id in model.navEvents() {
                leaveCreateScreen()
                router.navigate(to: .sessionDetail(sessionId: id))
            }
        }
        .task {
            // The wizard stays beneath the setup terminal, so Back returns to it.
            for await id in model.setupNavEvents() {
                router.navigate(to: .sessionDetail(sessionId: id))
            }
        }
        .task {
            for await workspaceName in model.autoScanNavEvents() {
                leaveCreateScreen()
                router.navigate(to: .autoScanDetail(workspaceName: workspaceName))
                model.clearAutoScanNav()
            }
        }
    }

    /// Opened from the terminal's sessions drawer this screen sits over a
    /// terminal, and a plain pop would leave that old terminal beneath the new
    /// screen; the list is restored instead (Android `navigateFromCreate`).
    /// Every other entry point (the list, the Workspaces shortcut) keeps the
    /// plain pop.
    private func leaveCreateScreen() {
        if case .sessionDetail = router.path.dropLast().last {
            router.popTo(.sessionList)
        } else {
            router.pop()
        }
    }

    private var isReordering: Bool { editMode.isEditing }

    /// The workspace list needs two rows to reorder; the mode list always has
    /// the five built-ins.
    private var canReorder: Bool {
        switch model.step {
        case .workspaceSelect: return model.workspaces.count > 1
        case .modePick: return true
        case .config: return false
        }
    }

    private var reorderFooter: String {
        isReordering
            ? "Drag ≡ to reorder, then tap Done. The order syncs to all your devices."
            : "Tap Reorder to change the order — it syncs to all your devices."
    }

    private var codexLoginPresented: Binding<Bool> {
        // Hiding only: the alert also closes after "Log in", before its Task
        // reads the key and the pending resume. Cancel clears them itself.
        Binding(get: { model.showCodexLogin }, set: { if !$0 { model.hideCodexLogin() } })
    }

    private var typeDeletePresented: Binding<Bool> {
        Binding(
            get: { model.typeDeleteTarget != nil },
            set: { if !$0 { model.typeDeleteTarget = nil } }
        )
    }

    // MARK: - Workspace select

    @ViewBuilder
    private var workspaceSelectStep: some View {
        Section {
            ForEach(model.workspaces, id: \.name) { workspace in
                selectableRow(workspace.name, selected: isStoredSelected(workspace.name)) {
                    guard !isReordering else { return }
                    model.selectStoredWorkspace(workspace.name)
                }
            }
            .onMove { source, destination in
                model.moveWorkspaces(fromOffsets: source, toOffset: destination)
            }
            selectableRow(
                "Custom path…",
                subtitle: "Run once — no workspace is created",
                selected: isCustomPathSelected
            ) {
                model.selectCustomPath()
            }
            Button {
                workspaceSheet = .create
            } label: {
                Label("Create new workspace…", systemImage: "plus")
            }
        } header: {
            PixelSectionHeader("Workspace")
        } footer: {
            if model.workspaces.count > 1 {
                PixelSectionFooter(reorderFooter)
            }
        }

        if isCustomPathSelected {
            Section(header: PixelSectionHeader("Directory")) {
                TextField("/absolute/path", text: Binding(
                    get: { model.customPathDraft },
                    set: { model.setCustomPath($0) }
                ))
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .pixelField(isInvalid: model.customPathError != nil)
                .focused($customPathFocused)
                .onChange(of: customPathFocused) { _, focused in pathSuggestions.focusChanged(focused) }
                .onChange(of: model.customPathDraft, initial: true) { _, text in pathSuggestions.textChanged(text) }
                .onAppear { pathSuggestions.probe() }
                PathSuggestionList(model: pathSuggestions) { name in
                    model.setCustomPath(pathSuggestions.accept(name))
                }
                if let error = model.customPathError {
                    Text(error).font(.footnote).foregroundStyle(colors.danger)
                } else {
                    Text(customPathExample + " — " + pathBrowseHint(windowsPaths: pathSuggestions.windowsPaths))
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                }
            }

            if !model.recentCustomPaths.isEmpty {
                Section(header: PixelSectionHeader("Recent paths")) {
                    ForEach(model.recentCustomPaths, id: \.self) { path in
                        recentPathRow(path)
                    }
                }
            }
        }

        Section {
            Button {
                model.continueFromWorkspace()
            } label: {
                Text("Continue")
            }
            .buttonStyle(.pixelPrimaryBlock)
            .disabled(!model.canContinueWorkspace)
        }
    }

    // MARK: - Mode pick

    @ViewBuilder
    private var modePickStep: some View {
        Section {
            // One list for built-in and custom cards, so a custom type can be
            // dragged above a built-in mode.
            ForEach(model.orderedModes, id: \.self) { mode in
                if case .custom(let id) = mode, let type = model.customType(id: id) {
                    customTypeRow(type)
                } else {
                    builtInModeRow(mode)
                }
            }
            .onMove { source, destination in
                model.moveModes(fromOffsets: source, toOffset: destination)
            }
            Button {
                // Always enabled — the 20-type cap is not gated client-side
                // (the cached list can be stale); the editor surfaces the
                // server's 409 inline instead.
                model.startCreateType()
            } label: {
                VStack(alignment: .leading, spacing: 3) {
                    Label("Add custom agent…", systemImage: "plus")
                        .foregroundStyle(colors.accent)
                    Text("Name + launch command, synced to your account")
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                }
            }
        } header: {
            PixelSectionHeader("Mode")
        } footer: {
            PixelSectionFooter(reorderFooter)
        }
    }

    /// A built-in mode card. Kept enabled while reordering, so an unavailable
    /// mode can still be dragged; the tap is ignored then instead.
    private func builtInModeRow(_ mode: CreateSessionModel.Mode) -> some View {
        Button {
            guard !isReordering else { return }
            model.selectMode(mode)
        } label: {
            HStack(spacing: 12) {
                if let icon = Self.modeIcon(mode) {
                    Image(systemName: icon)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(model.isEnabled(mode) ? colors.accent : colors.gray)
                        .frame(width: 24)
                        .accessibilityHidden(true)
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text(model.modeTitle(mode))
                        .foregroundStyle(model.isEnabled(mode) ? colors.text : colors.gray)
                    Text(model.reason(for: mode))
                        .font(.caption)
                        .foregroundStyle(model.isEnabled(mode) ? colors.textMuted : colors.warning)
                }
                Spacer()
                if !isReordering {
                    modeChevron
                }
            }
        }
        .disabled(!model.isEnabled(mode) && !isReordering)
    }

    /// The leading symbol of a built-in mode row; the agent rows have none.
    private static func modeIcon(_ mode: CreateSessionModel.Mode) -> String? {
        switch mode {
        case .shell: return "terminal"
        case .autoScan: return "target"
        default: return nil
        }
    }

    /// Trailing disclosure chevron: tapping a mode row commits and moves on
    /// (the mode step has no confirm button). Not shown on "Add custom
    /// agent…", which opens the editor rather than advancing the wizard.
    private var modeChevron: some View {
        Image(systemName: "chevron.right")
            .font(.caption.weight(.semibold))
            .foregroundStyle(colors.gray)
    }

    /// Mode card for one account-scoped custom agent type. Tap selects the
    /// mode; the context menu carries Edit/Delete (iOS affordance for
    /// Android's long-press menu). Never `.disabled` — a disabled row would
    /// also swallow the context menu (the `SavedShortcutKeyButton` rule), and
    /// custom modes are always launchable anyway.
    private func customTypeRow(_ type: CustomAgentTypeDto) -> some View {
        Button {
            guard !isReordering else { return }
            model.selectMode(.custom(type.id))
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(type.name)
                        .foregroundStyle(colors.text)
                    Text(type.command)
                        .font(.caption.monospaced())
                        .foregroundStyle(colors.textMuted)
                        .lineLimit(1)
                }
                Spacer()
                if !isReordering {
                    modeChevron
                }
            }
        }
        .contextMenu {
            Button {
                model.startEditType(type)
            } label: {
                Label("Edit", systemImage: "pencil")
            }
            Button(role: .destructive) {
                model.typeDeleteTarget = type
            } label: {
                Label("Delete", systemImage: "trash")
            }
        }
    }

    // MARK: - Config

    /// Auto Scan is split out ahead of the session-creating modes: it forces
    /// `worktree` on (so the Worktree section would be a lie) and starts a scan
    /// rather than a session, so it carries its own submit button.
    @ViewBuilder
    private var configStep: some View {
        if model.mode == .autoScan {
            autoScanConfig
        } else {
            switch model.mode {
            case .standard: claudeConfig
            case .codex: codexConfig
            case .antigravity: antigravityConfig
            case .opencode: opencodeConfig
            case .copilot: copilotConfig
            case .cursor: cursorConfig
            case .junie: junieConfig
            case .pi: piConfig
            case .custom(let id): customConfig(id)
            case .autoScan, .shell, .none: shellConfig
            }
            gitOptionsSection
            if model.mode == .standard {
                claudeAdvancedSection
            }
            if model.mode == .codex {
                codexAdvancedSection
            }
            Section {
                Button {
                    Task { await model.create() }
                } label: {
                    HStack(spacing: 10) {
                        Text(model.mode == .shell ? "Start Terminal" : "Create Session")
                        if model.submitting {
                            PixelLoadingBlock(width: 7, height: 13)
                        }
                    }
                }
                .buttonStyle(.pixelPrimaryBlock)
                .disabled(!model.canCreate)
            }
        }
    }

    /// An agent with `capabilities.autoscan` gets the templates, the source,
    /// filter, agent and agent-option choices; an older one keeps the Linear +
    /// Claude Code panel. The step waits for the probe so it does not switch
    /// panels under the user.
    @ViewBuilder
    private var autoScanConfig: some View {
        let setup = model.autoScanSetup
        if !setup.capabilityProbed {
            Section {
                HStack(spacing: 10) {
                    PixelLoadingBlock(width: 7, height: 13)
                    Text("Checking the agent on your PC…")
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                }
            }
        } else if setup.isAvailable {
            AutoScanIntroSection()
            AutoScanTemplatesSection(
                setup: setup,
                onSave: {
                    model.autoScanSetup.clearTemplateActionError()
                    saveTemplateShown = true
                },
                onDelete: { templateToDelete = $0 }
            )
            AutoScanHowItWorksSection(setup: setup)
            AutoScanSourceSections(
                setup: setup,
                setupStarting: model.githubSetupStarting,
                tipsHidden: Binding(
                    get: { model.autoScanTipsHidden },
                    set: { model.setAutoScanTipsHidden($0) }
                )
            ) { action in
                Task { await model.runGitHubSetup(action) }
            }
            AutoScanAgentSection(setup: setup)
            // Before the options: choosing Chat hides Claude's Focus view row,
            // which must not move these rows under the finger.
            AutoScanSessionViewSection(setup: setup)
            autoScanAgentOptions
            autoScanInstructionsSection
            autoScanStartSection
        } else {
            autoScanClaudeSections(legacy: true)
            autoScanPromptSection
            autoScanStartSection
        }
    }

    /// The chosen agent's options, from Auto Scan's own buckets; the controls
    /// are a normal session's, without the chat, worktree and sign-in parts.
    @ViewBuilder
    private var autoScanAgentOptions: some View {
        @Bindable var setup = model.autoScanSetup
        switch setup.cli {
        case "claude"?:
            autoScanClaudeSections(legacy: false)
        case "codex"?:
            Section {
                CodexAutonomyRows(preset: setup.codexOptions.preset) { setup.codexOptions.apply($0) }
                mayAskHint("codex")
            } header: {
                PixelSectionHeader("Codex autonomy")
            } footer: {
                if setup.codexOptions.preset == nil {
                    PixelSectionFooter("Custom sandbox/approval set in Advanced.")
                }
            }
            Section(header: PixelSectionHeader("Codex")) {
                CodexModelEffortRows(
                    model: Binding(
                        get: { setup.codexOptions.model },
                        set: { model.setAutoScanCodexModel($0) }
                    ),
                    optionalEffort: $setup.codexOptions.reasoning_effort,
                    modelOptions: model.codexModelOptions(selected: setup.codexOptions.model),
                    advertisedEfforts: model.codexEfforts(for: setup.codexOptions.model)
                )
            }
            Section {
                DisclosureGroup("Advanced", isExpanded: $autoScanCodexAdvancedExpanded) {
                    CodexSandboxApprovalRows(
                        sandbox: $setup.codexOptions.sandbox,
                        approval: $setup.codexOptions.approval,
                        fullAuto: setup.codexOptions.full_auto
                    )
                }
            }
        case "antigravity"?:
            Section(header: PixelSectionHeader("Antigravity")) {
                AntigravityOptionRows(
                    model: $setup.antigravityOptions.model,
                    models: model.antigravityModels,
                    skipPermissions: Binding(
                        get: { setup.antigravityOptions.skip_permissions },
                        set: { setup.setAntigravitySkipPermissions($0) }
                    ),
                    sandbox: Binding(
                        get: { setup.antigravityOptions.sandbox },
                        set: { setup.setAntigravitySandbox($0) }
                    ),
                    addDirs: $setup.antigravityAddDirsDraft,
                    skipPermissionsHint: setup.agentMayAsk("antigravity") ? Self.mayAskText : nil
                )
            }
        case "opencode"?:
            Section(header: PixelSectionHeader("OpenCode")) {
                OpenCodeOptionRows(autoApprove: $setup.opencodeOptions.auto_approve)
                mayAskHint("opencode")
                codeModeToggle($setup.opencodeOptions.code_mode, hint: Self.opencodeCodeModeHint)
            }
        case "copilot"?:
            Section(header: PixelSectionHeader("GitHub Copilot")) {
                CopilotOptionRows(allowAll: $setup.copilotOptions.allow_all)
                mayAskHint("copilot")
            }
        case "cursor"?:
            Section(header: PixelSectionHeader("Cursor")) {
                CursorOptionRows(force: $setup.cursorOptions.force)
                mayAskHint("cursor")
            }
        case "junie"?:
            Section(header: PixelSectionHeader("Junie")) {
                JunieOptionRows(planMode: $setup.junieOptions.plan_mode, brave: $setup.junieOptions.brave)
                mayAskHint("junie")
            }
        case "pi"?:
            Section(header: PixelSectionHeader("Pi")) {
                Text("Pi runs its tools without asking.")
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
                codeModeToggle($setup.piOptions.code_mode, hint: Self.piCodeModeHint)
            }
        case .some:
            EmptyView()
        case nil:
            EmptyView()
        }
    }

    private static let mayAskText = "When off, the agent may stop and wait for your approval. You get a notification and can open the session to approve."
    /// Codex has no on/off switch: its presets decide whether it asks.
    private static let codexMayAskText = "Without Full access, Codex may stop and wait for your approval. You get a notification and can open the session to approve."

    @ViewBuilder
    private func mayAskHint(_ kind: String) -> some View {
        if model.autoScanSetup.agentMayAsk(kind) {
            Text(kind == "codex" ? Self.codexMayAskText : Self.mayAskText)
                .font(.footnote)
                .foregroundStyle(colors.warning)
        }
    }

    /// Claude Code's options for Auto Scan, from the Auto Scan bucket. `legacy`
    /// is the older-agent panel, which still says where each issue runs.
    @ViewBuilder
    private func autoScanClaudeSections(legacy: Bool) -> some View {
        @Bindable var model = model
        Section {
            claudeModelAndEffortPickers(
                claudeModel: $model.autoScanClaudeModel,
                effort: $model.autoScanClaudeEffort,
                fastMode: model.autoScanClaudeFastMode
            )
            Toggle("Plan mode", isOn: $model.autoScanPlanMode)
            // Focus view only shapes Claude Code's terminal app.
            if legacy || model.autoScanSetup.effectiveSessionView == .terminal {
                focusViewToggle($model.autoScanFocusMode)
            }
            Toggle("Skip permissions", isOn: $model.autoScanSkipPermissions)
            if !model.autoScanSkipPermissions {
                Text(Self.mayAskText)
                    .font(.footnote)
                    .foregroundStyle(colors.warning)
            }
        } header: {
            PixelSectionHeader(legacy ? "Claude" : "Claude Code")
        } footer: {
            if legacy {
                PixelSectionFooter("Each matching issue runs in its own git worktree, so its changes stay isolated from your working copy. Non-git workspaces run in the workspace directory.")
            }
        }

        claudeAdvancedOptions(
            isExpanded: $autoScanAdvancedExpanded,
            codeMode: legacy ? nil : Binding(
                get: { model.autoScanSetup.claudeCodeMode },
                set: { model.autoScanSetup.claudeCodeMode = $0 }
            ),
            showsDebug: true,
            noMemory: $model.autoScanNoMemory,
            fastMode: $model.autoScanClaudeFastMode,
            ultracode: $model.autoScanClaudeUltracode,
            debug: $model.autoScanClaudeDebug,
            appendSystemPrompt: $model.autoScanClaudeAppendSystemPrompt
        )
    }

    /// Step 4 for an agent with `capabilities.autoscan`.
    @ViewBuilder
    private var autoScanInstructionsSection: some View {
        @Bindable var model = model
        Section {
            VStack(alignment: .leading, spacing: 6) {
                Text("Instructions for the agent (optional)").sectionHeading()
                TextField("For example: add a test for each fix.", text: $model.autoScanInitialPrompt, axis: .vertical)
                    .lineLimit(3...8)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .pixelField()
                Text("Your playbook for every issue. The issue's title, link and description are added after it.")
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
            }
            .padding(.vertical, 4)
            AutoScanIntervalRows(setup: model.autoScanSetup)
        } header: {
            AutoScanStepHeader(4, "Instructions and schedule")
        }
    }

    /// The older-agent panel's prompt.
    @ViewBuilder
    private var autoScanPromptSection: some View {
        @Bindable var model = model
        Section {
            TextField("Sent to each spawned session", text: $model.autoScanInitialPrompt, axis: .vertical)
                .lineLimit(3...8)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .pixelField()
        } header: {
            PixelSectionHeader("Initial prompt")
        }
    }

    @ViewBuilder
    private var autoScanStartSection: some View {
        Section {
            Button {
                Task { await model.startAutoScan() }
            } label: {
                HStack(spacing: 10) {
                    Text("Start Auto Scan")
                    if model.autoScanStarting {
                        PixelLoadingBlock(width: 7, height: 13)
                    }
                }
            }
            .buttonStyle(.pixelPrimaryBlock)
            .disabled(!model.canStartAutoScan)
        } footer: {
            VStack(alignment: .leading, spacing: 6) {
                if let startError = model.autoScanStartError {
                    PixelSectionFooter(startError, tone: .danger)
                }
                if model.autoScanSetup.isAvailable {
                    if let blocker = model.autoScanSetup.startBlocker(prompt: model.autoScanInitialPrompt) {
                        PixelSectionFooter(blocker, tone: .warning)
                    }
                } else {
                    if !model.claudeEnabled {
                        PixelSectionFooter(model.reason(for: .autoScan), tone: .warning)
                    }
                    PixelSectionFooter("Update Mobaide on your PC to scan GitHub issues and choose the agent.")
                }
            }
        }
    }

    @ViewBuilder
    private var claudeConfig: some View {
        @Bindable var model = model
        sessionViewSection(
            $model.terminalUI,
            usesChat: model.claudeUsesChat,
            unavailableReason: model.chatUnavailableReason ?? model.claudeCodeModeChatNote
        )
        Section(header: PixelSectionHeader("Claude")) {
            claudeModelAndEffortPickers(
                claudeModel: $model.claudeModel,
                effort: $model.claudeEffort,
                fastMode: model.claudeFastMode
            )
            // Focus view only shapes Claude Code's terminal TUI.
            if !model.claudeUsesChat {
                focusViewToggle($model.focusMode)
            }
            Toggle("Skip permissions", isOn: $model.skipPermissions)
        }
        continueSessionSection(agentName: "Claude Code")
    }

    /// "Continue a session" for the agents whose sessions open in the chat view.
    @ViewBuilder
    private func continueSessionSection(
        agentName: String,
        hint: String? = nil
    ) -> some View {
        if model.canContinueSession {
            configEntrySection(
                systemImage: "clock.arrow.circlepath",
                title: "Continue a session",
                hint: hint ?? "Open a \(agentName) session started on your PC, for example in a terminal."
            ) {
                showContinueSession = true
            }
        }
    }

    private func configEntrySection(
        systemImage: String,
        title: String,
        hint: String,
        action: @escaping () -> Void
    ) -> some View {
        Section {
            Button(action: action) {
                HStack(spacing: 10) {
                    Image(systemName: systemImage)
                        .foregroundStyle(colors.accent)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(title)
                            .foregroundStyle(colors.text)
                        Text(hint)
                            .font(.caption)
                            .foregroundStyle(colors.textMuted)
                    }
                    Spacer()
                    if model.submitting {
                        PixelLoadingBlock(width: 7, height: 13)
                    } else {
                        Image(systemName: "chevron.right")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(colors.gray)
                    }
                }
                .contentShape(Rectangle())
            }
            .disabled(model.submitting)
        }
    }

    /// "Open sessions in" for a normal agent session: Chat is `terminalUI`
    /// off. Placed first, so choosing a view never moves the rows below it.
    private func sessionViewSection(
        _ terminalUI: Binding<Bool>,
        usesChat: Bool,
        unavailableReason: String?
    ) -> some View {
        SessionViewChooserSection(
            chatSelected: usesChat,
            chatUnavailableReason: unavailableReason,
            onChoose: { terminalUI.wrappedValue = !$0 }
        )
    }

    /// "Code Mode": the agent writes JavaScript that makes several tool calls in one step.
    @ViewBuilder
    private func codeModeToggle(_ isOn: Binding<Bool>, hint: String) -> some View {
        Toggle("Code Mode", isOn: isOn)
        Text(hint)
            .font(.footnote)
            .foregroundStyle(colors.textMuted)
    }

    /// Rendered after the Worktree section, so the main options stay on top.
    @ViewBuilder
    private var claudeAdvancedSection: some View {
        @Bindable var model = model
        claudeAdvancedOptions(
            isExpanded: $claudeAdvancedExpanded,
            codeMode: $model.claudeCodeMode,
            showsDebug: !model.claudeUsesChat,
            noMemory: $model.noMemory,
            fastMode: $model.claudeFastMode,
            ultracode: $model.claudeUltracode,
            debug: $model.claudeDebug,
            appendSystemPrompt: $model.claudeAppendSystemPrompt
        )
    }

    @ViewBuilder
    private func focusViewToggle(_ isOn: Binding<Bool>) -> some View {
        Toggle("Focus view", isOn: isOn)
        Text("Claude Code shows only your last prompt, a one-line summary of tool calls, and the final response.")
            .font(.footnote)
            .foregroundStyle(colors.textMuted)
    }

    /// Model + Effort pickers with the fast-mode model interlock.
    /// Shared by the Claude Code and Auto Scan config steps, which drive the
    /// same controls from separate option buckets (Android shares these as
    /// `ClaudeOptionControls`).
    @ViewBuilder
    private func claudeModelAndEffortPickers(
        claudeModel: Binding<String?>,
        effort: Binding<String>,
        fastMode: Bool
    ) -> some View {
        Picker("Model", selection: claudeModel) {
            Text("Default").tag(String?.none)
            ForEach(CreateSessionModel.claudeModels, id: \.self) { Text($0).tag(String?.some($0)) }
        }
        .disabled(fastMode)

        Picker("Effort", selection: effort) {
            ForEach(model.claudeEfforts(for: claudeModel.wrappedValue), id: \.self) { Text($0).tag($0) }
        }
    }

    /// Collapsed-by-default "Advanced" section shared by the Claude Code and
    /// Auto Scan steps (Android: `ClaudeAdvancedOptions`). The older-agent
    /// Auto Scan panel passes nil for `codeMode`.
    @ViewBuilder
    private func claudeAdvancedOptions(
        isExpanded: Binding<Bool>,
        codeMode: Binding<Bool>?,
        showsDebug: Bool,
        noMemory: Binding<Bool>,
        fastMode: Binding<Bool>,
        ultracode: Binding<Bool>,
        debug: Binding<Bool>,
        appendSystemPrompt: Binding<String>
    ) -> some View {
        Section {
            DisclosureGroup("Advanced", isExpanded: isExpanded) {
                if let codeMode {
                    codeModeToggle(codeMode, hint: Self.claudeCodeModeHint)
                }
                Toggle("No memory", isOn: noMemory)
                Toggle("Fast mode (Opus only)", isOn: fastMode)
                Toggle("Ultracode", isOn: ultracode)
                if showsDebug {
                    Toggle("Debug logging", isOn: debug)
                    Text("Claude Code prints verbose diagnostics into the terminal.")
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                }
                TextField("Append system prompt", text: appendSystemPrompt, axis: .vertical)
                    .lineLimit(1...4)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .pixelField()
            }
        }
    }

    @ViewBuilder
    private var codexConfig: some View {
        @Bindable var model = model
        sessionViewSection(
            $model.codexTerminalUI,
            usesChat: model.codexUsesChat,
            unavailableReason: model.codexChatUnavailableReason
        )
        // Sandbox and approval live under Advanced, so the preset rows show
        // which one the current pair matches (none for a hand-picked pair).
        Section {
            CodexAutonomyRows(preset: model.codexPreset) { model.applyCodexPreset($0) }
        } header: {
            PixelSectionHeader("Autonomy")
        } footer: {
            if model.codexPreset == nil {
                PixelSectionFooter("Custom sandbox/approval set in Advanced.")
            }
        }
        Section(header: PixelSectionHeader("Codex")) {
            // Routed through setCodexModel (not a bare binding) so a stranded
            // effort re-defaults when the new model's advertised set lacks it —
            // the setAntigravitySkipPermissions Binding precedent.
            CodexModelEffortRows(
                model: Binding(get: { model.codexModel }, set: { model.setCodexModel($0) }),
                effort: $model.codexReasoningEffort,
                modelOptions: model.codexModelOptions,
                advertisedEfforts: model.codexEfforts(for: model.codexModel)
            )
        }
        continueSessionSection(agentName: "Codex")
    }

    /// Collapsed like the Claude Code one; rendered after the Worktree section.
    @ViewBuilder
    private var codexAdvancedSection: some View {
        @Bindable var model = model
        Section {
            DisclosureGroup("Advanced", isExpanded: $codexAdvancedExpanded) {
                CodexSandboxApprovalRows(
                    sandbox: $model.codexSandbox,
                    approval: $model.codexApproval,
                    fullAuto: model.codexFullAuto
                )
            }
        }
    }

    @ViewBuilder
    private var antigravityConfig: some View {
        @Bindable var model = model
        sessionViewSection(
            $model.antigravityTerminalUI,
            usesChat: model.antigravityUsesChat,
            unavailableReason: model.antigravityChatUnavailableReason
        )
        Section(header: PixelSectionHeader("Antigravity")) {
            AntigravityOptionRows(
                model: $model.antigravityModel,
                models: model.antigravityModels,
                skipPermissions: Binding(
                    get: { model.antigravitySkipPermissions },
                    set: { model.setAntigravitySkipPermissions($0) }
                ),
                sandbox: Binding(
                    get: { model.antigravitySandbox },
                    set: { model.setAntigravitySandbox($0) }
                ),
                addDirs: $model.antigravityAddDirs
            )
        }
        continueSessionSection(
            agentName: "Antigravity",
            hint: "Open an Antigravity conversation started on your PC, for example with agy in a terminal."
        )
    }

    @ViewBuilder
    private var opencodeConfig: some View {
        @Bindable var model = model
        sessionViewSection(
            $model.opencodeTerminalUI,
            usesChat: model.opencodeUsesChat,
            unavailableReason: model.opencodeChatUnavailableReason
        )
        Section(header: PixelSectionHeader("OpenCode")) {
            OpenCodeOptionRows(autoApprove: $model.opencodeAutoApprove)
            codeModeToggle($model.opencodeCodeMode, hint: Self.opencodeCodeModeHint)
        }
        continueSessionSection(
            agentName: "OpenCode",
            hint: "Open an OpenCode session started on your PC, for example in a terminal."
        )
        configEntrySection(
            systemImage: "person.badge.key",
            title: "Connect a provider",
            hint: "Sign in to a model provider, for example Anthropic, OpenAI or Google. "
                + "OpenCode’s sign-in opens in a terminal and stays on your PC."
        ) {
            Task { await model.connectOpenCodeProvider() }
        }
    }

    @ViewBuilder
    private var copilotConfig: some View {
        @Bindable var model = model
        sessionViewSection(
            $model.copilotTerminalUI,
            usesChat: model.copilotUsesChat,
            unavailableReason: model.copilotChatUnavailableReason
        )
        Section(header: PixelSectionHeader("GitHub Copilot")) {
            CopilotOptionRows(allowAll: $model.copilotAllowAll)
        }
        continueSessionSection(agentName: "GitHub Copilot")
    }

    /// Pi has no option to set at launch: say what a user of the other agents
    /// would look for here.
    @ViewBuilder
    private var piConfig: some View {
        @Bindable var model = model
        sessionViewSection(
            $model.piTerminalUI,
            usesChat: model.piUsesChat,
            unavailableReason: model.piChatUnavailableReason
        )
        Section(header: PixelSectionHeader("Pi")) {
            Text(
                "Pi runs commands and edits files without asking. "
                    + "Pick the model and the thinking level inside the session."
            )
            .font(.footnote)
            .foregroundStyle(colors.textMuted)
            codeModeToggle($model.piCodeMode, hint: Self.piCodeModeHint)
        }
        continueSessionSection(agentName: "Pi")
    }

    @ViewBuilder
    private var cursorConfig: some View {
        @Bindable var model = model
        sessionViewSection(
            $model.cursorTerminalUI,
            usesChat: model.cursorUsesChat,
            unavailableReason: model.cursorChatUnavailableReason
        )
        Section(header: PixelSectionHeader("Cursor")) {
            CursorOptionRows(force: $model.cursorForce)
        }
        continueSessionSection(
            agentName: "Cursor",
            hint: "Open a Cursor chat session stored on your PC. Sessions from Cursor’s terminal UI are not listed."
        )
    }

    @ViewBuilder
    private var junieConfig: some View {
        @Bindable var model = model
        sessionViewSection(
            $model.junieTerminalUI,
            usesChat: model.junieUsesChat,
            unavailableReason: model.junieChatUnavailableReason
        )
        Section(header: PixelSectionHeader("Junie")) {
            JunieOptionRows(planMode: $model.juniePlanMode, brave: $model.junieBrave)
        }
        continueSessionSection(
            agentName: "Junie",
            hint: "Open a Junie session stored on your PC, also one you started in Junie’s terminal UI."
        )
    }

    @ViewBuilder
    private var shellConfig: some View {
        Section(header: PixelSectionHeader("Terminal")) {
            Text("Bash / cmd in the chosen directory")
                .font(.footnote)
                .foregroundStyle(colors.textMuted)
        }
    }

    /// Terminal-style config section for a custom agent type: the type's live
    /// name as the header and its command as the body (falls back to the
    /// plain-terminal wording when the type was deleted remotely mid-wizard —
    /// the launch then surfaces the relay's 404).
    @ViewBuilder
    private func customConfig(_ id: String) -> some View {
        Section(header: PixelSectionHeader(model.modeTitle(.custom(id)))) {
            Text(model.customType(id: id)?.command ?? "Bash / cmd in the chosen directory")
                .font(.footnote.monospaced())
                .foregroundStyle(colors.textMuted)
                .lineLimit(3)
        }
    }

    @ViewBuilder
    private var gitOptionsSection: some View {
        if model.showsGitOptions {
            @Bindable var model = model
            Section(header: PixelSectionHeader("Worktree")) {
                Toggle("Create worktree", isOn: $model.worktree)
                if model.worktree {
                    TextField("Branch name (optional)", text: $model.branchName)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .pixelField()
                }
                if model.showsBaseBranchPicker {
                    baseBranchRow
                }
            }
        }
    }

    @ViewBuilder
    private var baseBranchRow: some View {
        switch model.worktreeBases {
        case .failed:
            HStack {
                Text("Couldn't load branches")
                    .font(.footnote)
                    .foregroundStyle(colors.danger)
                Spacer()
                Button("Retry") {
                    Task { await model.loadWorktreeBases(force: true) }
                }
                .buttonStyle(.borderless)
            }
        case .loaded:
            Button {
                baseBranchPickerShown = true
            } label: {
                baseBranchLabel(WorktreeBaseDisplay.label(model.baseBranch))
            }
            .accessibilityIdentifier("create-session-base-branch")
        case .idle, .loading:
            baseBranchLabel("Loading branches…")
                .disabled(true)
        }
    }

    private func baseBranchLabel(_ value: String) -> some View {
        HStack {
            Text("Base branch").foregroundStyle(colors.text)
            Spacer()
            Text(value)
                .font(.body.monospaced())
                .foregroundStyle(colors.textMuted)
                .lineLimit(1)
                .truncationMode(.middle)
            Image(systemName: "chevron.right")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(colors.textMuted)
        }
        .contentShape(Rectangle())
    }

    // MARK: - Helpers

    private func selectableRow(
        _ title: String,
        subtitle: String? = nil,
        selected: Bool,
        action: @escaping () -> Void
    ) -> some View {
        SelectableRow(title: title, subtitle: subtitle, selected: selected, action: action)
    }

    /// Tap fills the field; swipe forgets the path. Head truncation keeps the
    /// tail visible — the trailing folder is what tells two long paths apart.
    private func recentPathRow(_ path: String) -> some View {
        Button {
            model.pickRecentCustomPath(path)
        } label: {
            Label {
                Text(path)
                    .font(.footnote.monospaced())
                    .lineLimit(1)
                    .truncationMode(.head)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } icon: {
                Image(systemName: "folder")
                    .foregroundStyle(colors.textMuted)
            }
        }
        .buttonStyle(.plain)
        .swipeActions(edge: .trailing) {
            deleteRecentButton(path)
        }
        .contextMenu {
            // Both affordances, as `GitScreen.row` does: swipe is the primary
            // gesture, long-press the discoverable one — and the only removal
            // path that does not depend on swipe behaving inside a `Form`.
            deleteRecentButton(path)
        }
    }

    private func deleteRecentButton(_ path: String) -> some View {
        Button(role: .destructive) {
            model.removeRecentCustomPath(path)
        } label: {
            Label("Delete", systemImage: "trash")
        }
    }

    private func isStoredSelected(_ name: String) -> Bool {
        if case .stored(let selected) = model.selection { return selected == name }
        return false
    }

    private var customPathExample: String {
        pathSuggestions.windowsPaths ? #"e.g. D:\projects\demo"# : "e.g. /home/user/code"
    }

    private var isCustomPathSelected: Bool {
        if case .customPath = model.selection { return true }
        return false
    }
}
