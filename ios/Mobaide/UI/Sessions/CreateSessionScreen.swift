import SwiftUI

/// Create-session wizard (Android `CreateSessionScreen`), the real body for the
/// `createSession` route: project select → mode pick (unavailable reasons in
/// `warningYellow`) → per-kind config.
struct CreateSessionScreen: View {
    /// Stored project to open directly in Auto Scan config (the Projects
    /// screen's "Start Auto Scan…" shortcut); nil for the normal wizard.
    var autoScanProject: String?

    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            CreateSessionContent(container: container, autoScanProject: autoScanProject)
        }
    }
}

private struct CreateSessionContent: View {
    @State private var model: CreateSessionModel
    @State private var toast: ToastMessage?
    @State private var projectSheet: ProjectFormMode?
    @State private var pathSuggestions: PathSuggestionsModel
    @FocusState private var customPathFocused: Bool

    @Environment(Router.self) private var router
    @Environment(\.mobaideColors) private var colors

    private let container: AppContainer

    init(container: AppContainer, autoScanProject: String?) {
        self.container = container
        _model = State(initialValue: CreateSessionModel(
            agentAPI: container.agentAPI,
            sessionRepository: container.sessionRepository,
            preferencesStore: container.preferencesStore,
            tokenStore: container.tokenStore,
            customAgentTypesRepository: container.customAgentTypesRepository,
            autoScanRepository: container.autoScanRepository,
            autoScanProject: autoScanProject
        ))
        _pathSuggestions = State(initialValue: PathSuggestionsModel(repository: container.pathSuggestionRepository))
    }

    var body: some View {
        @Bindable var model = model
        List {
            Group {
                switch model.step {
                case .projectSelect: projectSelectStep
                case .modePick: modePickStep
                case .config: configStep
                }
            }
            .pixelFormSection()
        }
        .pixelForm()
        .navigationTitle("New Session")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(model.step != .projectSelect)
        .toolbar {
            if model.step != .projectSelect {
                ToolbarItem(placement: .topBarLeading) {
                    Button { model.back() } label: {
                        Label("Back", systemImage: "chevron.backward")
                    }
                }
            }
        }
        .toast($toast)
        .sheet(item: $projectSheet) { mode in
            ProjectFormSheet(mode: mode, container: container, onCreated: { model.onProjectCreated($0) })
        }
        .task { await model.load() }
        .task { await model.observeCustomTypes() }
        .task { await model.observeScans() }
        .onChange(of: model.step) { _, step in
            if step == .modePick {
                Task { await model.refreshCliAvailability() }
                Task { await model.refreshCustomTypes() }
            }
        }
        .sheet(item: $model.typeEditorTarget) { target in
            CustomAgentTypeEditorSheet(model: model, target: target.type)
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
                router.pop()
                router.navigate(to: .sessionDetail(sessionId: id))
            }
        }
        .task {
            for await projectName in model.autoScanNavEvents() {
                router.pop()
                router.navigate(to: .autoScanDetail(projectName: projectName))
                model.clearAutoScanNav()
            }
        }
    }

    private var codexLoginPresented: Binding<Bool> {
        Binding(get: { model.showCodexLogin }, set: { if !$0 { model.cancelCodexLogin() } })
    }

    private var typeDeletePresented: Binding<Bool> {
        Binding(
            get: { model.typeDeleteTarget != nil },
            set: { if !$0 { model.typeDeleteTarget = nil } }
        )
    }

    // MARK: - Project select

    @ViewBuilder
    private var projectSelectStep: some View {
        Section(header: PixelSectionHeader("Project")) {
            ForEach(model.projects, id: \.name) { project in
                selectableRow(project.name, selected: isStoredSelected(project.name)) {
                    model.selectStoredProject(project.name)
                }
            }
            selectableRow(
                "Custom path…",
                subtitle: "Run once — no project is created",
                selected: isCustomPathSelected
            ) {
                model.selectCustomPath()
            }
            Button {
                projectSheet = .create
            } label: {
                Label("Create new project…", systemImage: "plus")
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
                PathSuggestionList(model: pathSuggestions) { name in
                    model.setCustomPath(pathSuggestions.accept(name))
                }
                if let error = model.customPathError {
                    Text(error).font(.footnote).foregroundStyle(colors.danger)
                } else {
                    Text(#"e.g. /home/user/code or D:\projects\demo"# + " — \(pathBrowseHint)")
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
                model.continueFromProject()
            } label: {
                Text("Continue")
            }
            .buttonStyle(.pixelPrimaryBlock)
            .disabled(!model.canContinueProject)
        }
    }

    // MARK: - Mode pick

    @ViewBuilder
    private var modePickStep: some View {
        Section(header: PixelSectionHeader("Mode")) {
            ForEach(CreateSessionModel.Mode.builtIn, id: \.self) { mode in
                Button { model.selectMode(mode) } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(model.modeTitle(mode))
                                .foregroundStyle(model.isEnabled(mode) ? colors.text : colors.gray)
                            Text(model.reason(for: mode))
                                .font(.caption)
                                .foregroundStyle(model.isEnabled(mode) ? colors.textMuted : colors.warning)
                        }
                        Spacer()
                        modeChevron
                    }
                }
                .disabled(!model.isEnabled(mode))
            }
            ForEach(model.customTypes) { type in
                customTypeRow(type)
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
                modeChevron
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
            case .custom(let id): customConfig(id)
            case .autoScan, .shell, .none: shellConfig
            }
            gitOptionsSection
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

    @ViewBuilder
    private var autoScanConfig: some View {
        @Bindable var model = model
        Section(header: PixelSectionHeader("Claude")) {
            claudeModelAndEffortPickers(
                claudeModel: $model.autoScanClaudeModel,
                effort: $model.autoScanClaudeEffort,
                fastMode: model.autoScanClaudeFastMode,
                ultracode: model.autoScanClaudeUltracode
            )
            claudeAdvancedControls(
                fastMode: $model.autoScanClaudeFastMode,
                ultracode: $model.autoScanClaudeUltracode,
                debug: $model.autoScanClaudeDebug,
                appendSystemPrompt: $model.autoScanClaudeAppendSystemPrompt
            )
        }

        Section {
            Toggle("Plan mode", isOn: $model.autoScanPlanMode)
            Toggle("Skip permissions", isOn: $model.autoScanSkipPermissions)
            // Shown in both states: this defaults ON, so leaving the caption to
            // the off-state would let an unattended permission-bypassing scan
            // start with no explanation at all.
            Text("When off, the scan pauses at permission prompts — you'll get a notification and can open the session to approve.")
                .font(.footnote)
                .foregroundStyle(colors.textMuted)
            Toggle("No memory", isOn: $model.autoScanNoMemory)
        } header: {
            PixelSectionHeader("Auto Scan")
        } footer: {
            PixelSectionFooter("Each matching issue runs in its own git worktree, so its changes stay isolated from your working copy. Non-git projects run in the project directory.")
        }

        Section(header: PixelSectionHeader("Initial prompt")) {
            TextField("Sent to each spawned session", text: $model.autoScanInitialPrompt, axis: .vertical)
                .lineLimit(3...8)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .pixelField()
        }

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
            if !model.claudeEnabled {
                PixelSectionFooter(model.reason(for: .autoScan), tone: .warning)
            }
        }
    }

    @ViewBuilder
    private var claudeConfig: some View {
        @Bindable var model = model
        Section(header: PixelSectionHeader("Claude")) {
            claudeModelAndEffortPickers(
                claudeModel: $model.claudeModel,
                effort: $model.claudeEffort,
                fastMode: model.claudeFastMode,
                ultracode: model.claudeUltracode
            )
            Toggle("Skip permissions", isOn: $model.skipPermissions)
            Toggle("No memory", isOn: $model.noMemory)
            claudeAdvancedControls(
                fastMode: $model.claudeFastMode,
                ultracode: $model.claudeUltracode,
                debug: $model.claudeDebug,
                appendSystemPrompt: $model.claudeAppendSystemPrompt
            )
        }
    }

    /// Model + Effort pickers with their fast-mode / ultracode interlocks.
    /// Shared by the Claude Code and Auto Scan config steps, which drive the
    /// same controls from separate option buckets (Android shares these as
    /// `ClaudeOptionControls`).
    @ViewBuilder
    private func claudeModelAndEffortPickers(
        claudeModel: Binding<String?>,
        effort: Binding<String>,
        fastMode: Bool,
        ultracode: Bool
    ) -> some View {
        Picker("Model", selection: claudeModel) {
            Text("Default").tag(String?.none)
            ForEach(CreateSessionModel.claudeModels, id: \.self) { Text($0).tag(String?.some($0)) }
        }
        .disabled(fastMode)

        Picker("Effort", selection: effort) {
            ForEach(model.claudeEfforts(for: claudeModel.wrappedValue), id: \.self) { Text($0).tag($0) }
        }
        .disabled(ultracode)
    }

    /// The trailing half of the shared Claude controls. Kept separate from the
    /// pickers so each config step can interleave its own toggles between the
    /// two runs without reordering anything on screen.
    @ViewBuilder
    private func claudeAdvancedControls(
        fastMode: Binding<Bool>,
        ultracode: Binding<Bool>,
        debug: Binding<Bool>,
        appendSystemPrompt: Binding<String>
    ) -> some View {
        Toggle("Fast mode (Opus only)", isOn: fastMode)
        Toggle("Ultracode", isOn: ultracode)
        Toggle("Debug logging", isOn: debug)
        Text("Claude Code prints verbose diagnostics into the terminal.")
            .font(.footnote)
            .foregroundStyle(colors.textMuted)
        TextField("Append system prompt", text: appendSystemPrompt, axis: .vertical)
            .lineLimit(1...4)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .pixelField()
    }

    @ViewBuilder
    private var codexConfig: some View {
        @Bindable var model = model
        Section(header: PixelSectionHeader("Autonomy")) {
            Button("Read only") { applyCodexPreset(sandbox: "read-only", approval: "on-request", fullAuto: false) }
            Button("Auto") { applyCodexPreset(sandbox: "workspace-write", approval: "on-request", fullAuto: false) }
            Button("Full access") { applyCodexPreset(sandbox: "danger-full-access", approval: "never", fullAuto: true) }
        }
        Section(header: PixelSectionHeader("Codex")) {
            // Routed through setCodexModel (not a bare binding) so a stranded
            // effort re-defaults when the new model's advertised set lacks it —
            // the setAntigravitySkipPermissions Binding precedent.
            Picker("Model", selection: Binding(
                get: { model.codexModel },
                set: { model.setCodexModel($0) }
            )) {
                Text("Default").tag(String?.none)
                // codexModelOptions, not codexModels: a restored model missing
                // from the list needs its tag or the Picker renders unselected
                // while the request still sends it.
                ForEach(model.codexModelOptions, id: \.self) { Text($0).tag(String?.some($0)) }
            }
            // Keep-selected repair: a restored-prefs or late-probe effort
            // outside the advertised set must stay in the options, or the
            // Picker renders blank while the request still sends the value.
            let advertisedEfforts = model.codexEfforts(for: model.codexModel)
            let effortOptions = advertisedEfforts.contains(model.codexReasoningEffort)
                ? advertisedEfforts : advertisedEfforts + [model.codexReasoningEffort]
            Picker("Reasoning effort", selection: $model.codexReasoningEffort) {
                ForEach(effortOptions, id: \.self) { Text($0).tag($0) }
            }
            Picker("Sandbox", selection: $model.codexSandbox) {
                ForEach(CreateSessionModel.codexSandboxes, id: \.self) { Text($0).tag($0) }
            }
            .disabled(model.codexFullAuto)
            Picker("Approval", selection: $model.codexApproval) {
                ForEach(CreateSessionModel.codexApprovals, id: \.self) { Text($0).tag($0) }
            }
            .disabled(model.codexFullAuto)
        }
    }

    @ViewBuilder
    private var antigravityConfig: some View {
        @Bindable var model = model
        Section(header: PixelSectionHeader("Antigravity")) {
            Picker("Model", selection: $model.antigravityModel) {
                Text("Auto").tag(String?.none)
                ForEach(model.antigravityModels, id: \.self) { Text($0).tag(String?.some($0)) }
            }
            Toggle("Skip permissions (dangerous)", isOn: Binding(
                get: { model.antigravitySkipPermissions },
                set: { model.setAntigravitySkipPermissions($0) }
            ))
            Toggle("Sandbox", isOn: Binding(
                get: { model.antigravitySandbox },
                set: { model.setAntigravitySandbox($0) }
            ))
            TextField("Additional directories", text: $model.antigravityAddDirs, axis: .vertical)
                .lineLimit(1...4)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .pixelField()
        }
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
            }
        }
    }

    // MARK: - Helpers

    private func applyCodexPreset(sandbox: String, approval: String, fullAuto: Bool) {
        model.codexSandbox = sandbox
        model.codexApproval = approval
        model.codexFullAuto = fullAuto
    }

    private func selectableRow(
        _ title: String,
        subtitle: String? = nil,
        selected: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).foregroundStyle(colors.text)
                    if let subtitle {
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(colors.textMuted)
                    }
                }
                Spacer()
                PixelRadio(isSelected: selected)
            }
        }
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

    private var isCustomPathSelected: Bool {
        if case .customPath = model.selection { return true }
        return false
    }
}
