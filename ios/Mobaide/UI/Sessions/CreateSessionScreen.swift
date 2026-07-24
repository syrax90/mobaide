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
            preferencesStore: container.preferencesStore
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
        .toast($toast)
        .sheet(item: $projectSheet) { mode in
            ProjectFormSheet(mode: mode, container: container, onCreated: { model.onProjectCreated($0) })
        }
        .task { await model.load() }
        .onChange(of: model.step) { _, step in
            if step == .modePick {
                Task { await model.refreshCliAvailability() }
            }
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
            Button("Continue") { model.continueFromProject() }
                .disabled(!model.canContinueProject)
        }
    }

    // MARK: - Mode pick

    @ViewBuilder
    private var modePickStep: some View {
        Section("Mode") {
            ForEach(CreateSessionModel.Mode.allCases, id: \.self) { mode in
                Button { model.selectMode(mode) } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(Self.modeTitle(mode))
                            .foregroundStyle(model.isEnabled(mode) ? colors.primary : .secondary)
                        Text(model.reason(for: mode))
                            .font(.caption)
                            .foregroundStyle(model.isEnabled(mode) ? Color.secondary : MobaideColors.warningYellow)
                    }
                }
                .disabled(!model.isEnabled(mode))
            }
        }
        Section {
            Button("Back") { model.back() }
        }
    }

    // MARK: - Config

    @ViewBuilder
    private var configStep: some View {
        switch model.mode {
        case .standard: claudeConfig
        case .codex: codexConfig
        case .antigravity: antigravityConfig
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
            .disabled(!model.canCreate)
            Button("Back") { model.back() }
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

    private static func modeTitle(_ mode: CreateSessionModel.Mode) -> String {
        switch mode {
        case .standard: return "Claude Code"
        case .codex: return "Codex"
        case .antigravity: return "Antigravity"
        case .shell: return "Terminal"
        }
    }
}
