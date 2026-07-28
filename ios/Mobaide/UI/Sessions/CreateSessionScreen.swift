import SwiftUI

/// Create-session wizard (Android `CreateSessionScreen`), the real body for the
/// `createSession` route: project select → mode pick (unavailable reasons in
/// `warningYellow`) → per-kind config.
struct CreateSessionScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            CreateSessionContent(container: container)
        }
    }
}

private struct CreateSessionContent: View {
    @State private var model: CreateSessionModel
    @State private var toast: ToastMessage?
    @State private var projectSheet: ProjectFormMode?

    @Environment(Router.self) private var router
    @Environment(\.mobaideColors) private var colors

    private let container: AppContainer

    init(container: AppContainer) {
        self.container = container
        _model = State(initialValue: CreateSessionModel(
            agentAPI: container.agentAPI,
            sessionRepository: container.sessionRepository,
            preferencesStore: container.preferencesStore,
            customAgentTypesRepository: container.customAgentTypesRepository
        ))
    }

    var body: some View {
        @Bindable var model = model
        Form {
            switch model.step {
            case .projectSelect: projectSelectStep
            case .modePick: modePickStep
            case .config: configStep
            }
        }
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
        Section("Project") {
            ForEach(model.projects, id: \.name) { project in
                selectableRow(project.name, selected: isStoredSelected(project.name)) {
                    model.selectStoredProject(project.name)
                }
            }
            selectableRow("Custom path…", selected: isCustomPathSelected) {
                model.setCustomPath(model.customPathDraft)
            }
            Button {
                projectSheet = .create
            } label: {
                Label("Create new project…", systemImage: "plus")
            }
        }

        if isCustomPathSelected {
            Section("Directory") {
                TextField("/absolute/path", text: Binding(
                    get: { model.customPathDraft },
                    set: { model.setCustomPath($0) }
                ))
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                if let error = model.customPathError {
                    Text(error).font(.footnote).foregroundStyle(colors.error)
                }
            }
        }

        Section {
            Button {
                model.continueFromProject()
            } label: {
                Text("Continue").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(!model.canContinueProject)
        }
    }

    // MARK: - Mode pick

    @ViewBuilder
    private var modePickStep: some View {
        Section("Mode") {
            ForEach(CreateSessionModel.Mode.builtIn, id: \.self) { mode in
                Button { model.selectMode(mode) } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(model.modeTitle(mode))
                            .foregroundStyle(model.isEnabled(mode) ? colors.primary : .secondary)
                        Text(model.reason(for: mode))
                            .font(.caption)
                            .foregroundStyle(model.isEnabled(mode) ? Color.secondary : MobaideColors.warningYellow)
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
                        .foregroundStyle(colors.primary)
                    Text("Name + launch command, synced to your account")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
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
            VStack(alignment: .leading, spacing: 3) {
                Text(type.name)
                    .foregroundStyle(colors.primary)
                Text(type.command)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
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

    @ViewBuilder
    private var configStep: some View {
        switch model.mode {
        case .standard: claudeConfig
        case .codex: codexConfig
        case .antigravity: antigravityConfig
        case .custom(let id): customConfig(id)
        case .shell, .none: shellConfig
        }
        gitOptionsSection
        Section {
            Button {
                Task { await model.create() }
            } label: {
                if model.submitting {
                    ProgressView().frame(maxWidth: .infinity)
                } else {
                    Text(model.mode == .shell ? "Start Terminal" : "Create Session")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(!model.canCreate)
        }
    }

    @ViewBuilder
    private var claudeConfig: some View {
        @Bindable var model = model
        Section("Claude") {
            Picker("Model", selection: $model.claudeModel) {
                Text("Default").tag(String?.none)
                ForEach(CreateSessionModel.claudeModels, id: \.self) { Text($0).tag(String?.some($0)) }
            }
            .disabled(model.claudeFastMode)

            Picker("Effort", selection: $model.claudeEffort) {
                ForEach(model.claudeEfforts(for: model.claudeModel), id: \.self) { Text($0).tag($0) }
            }
            .disabled(model.claudeUltracode)

            Toggle("Skip permissions", isOn: $model.skipPermissions)
            Toggle("No memory", isOn: $model.noMemory)
            Toggle("Fast mode (Opus only)", isOn: $model.claudeFastMode)
            Toggle("Ultracode", isOn: $model.claudeUltracode)
            TextField("Append system prompt", text: $model.claudeAppendSystemPrompt, axis: .vertical)
                .lineLimit(1...4)
        }
    }

    @ViewBuilder
    private var codexConfig: some View {
        @Bindable var model = model
        Section("Autonomy") {
            Button("Read only") { applyCodexPreset(sandbox: "read-only", approval: "on-request", fullAuto: false) }
            Button("Auto") { applyCodexPreset(sandbox: "workspace-write", approval: "on-request", fullAuto: false) }
            Button("Full access") { applyCodexPreset(sandbox: "danger-full-access", approval: "never", fullAuto: true) }
        }
        Section("Codex") {
            Picker("Model", selection: $model.codexModel) {
                Text("Default").tag(String?.none)
                ForEach(model.codexModels, id: \.self) { Text($0).tag(String?.some($0)) }
            }
            Picker("Reasoning effort", selection: $model.codexReasoningEffort) {
                ForEach(CreateSessionModel.reasoningEfforts, id: \.self) { Text($0).tag($0) }
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
        Section("Antigravity") {
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
        }
    }

    @ViewBuilder
    private var shellConfig: some View {
        Section("Terminal") {
            Text("Bash / cmd in the chosen directory")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    /// Terminal-style config section for a custom agent type: the type's live
    /// name as the header and its command as the body (falls back to the
    /// plain-terminal wording when the type was deleted remotely mid-wizard —
    /// the launch then surfaces the relay's 404).
    @ViewBuilder
    private func customConfig(_ id: String) -> some View {
        Section(model.modeTitle(.custom(id))) {
            Text(model.customType(id: id)?.command ?? "Bash / cmd in the chosen directory")
                .font(.footnote.monospaced())
                .foregroundStyle(.secondary)
                .lineLimit(3)
        }
    }

    @ViewBuilder
    private var gitOptionsSection: some View {
        if model.showsGitOptions {
            @Bindable var model = model
            Section("Worktree") {
                Toggle("Create worktree", isOn: $model.worktree)
                if model.worktree {
                    TextField("Branch name (optional)", text: $model.branchName)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
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

    private func selectableRow(_ title: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Text(title).foregroundStyle(colors.primary)
                Spacer()
                if selected {
                    Image(systemName: "checkmark").foregroundStyle(colors.primary)
                }
            }
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
