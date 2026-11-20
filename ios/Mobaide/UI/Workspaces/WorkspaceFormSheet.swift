import SwiftUI

/// Create/edit workspace form (Android `WorkspaceFormSheet`). Presented as a
/// medium/large sheet; blocks interactive dismissal while dirty so edits are
/// not lost by an accidental swipe-down.
struct WorkspaceFormSheet: View {
    let mode: WorkspaceFormMode
    let container: AppContainer
    var onOpenGit: (String) -> Void = { _ in }
    var onCreated: (WorkspaceInfo) -> Void = { _ in }

    var body: some View {
        WorkspaceFormContent(mode: mode, container: container, onOpenGit: onOpenGit, onCreated: onCreated)
    }
}

private struct WorkspaceFormContent: View {
    @State private var model: WorkspaceFormModel
    @State private var revealKey = false
    @State private var toast: ToastMessage?
    @State private var pathSuggestions: PathSuggestionsModel
    @FocusState private var pathFocused: Bool

    @Environment(\.dismiss) private var dismiss
    @Environment(\.mobaideColors) private var colors

    private let onOpenGit: (String) -> Void
    private let onCreated: (WorkspaceInfo) -> Void
    private let agentAPI: AgentAPI

    init(
        mode: WorkspaceFormMode,
        container: AppContainer,
        onOpenGit: @escaping (String) -> Void,
        onCreated: @escaping (WorkspaceInfo) -> Void
    ) {
        _model = State(initialValue: WorkspaceFormModel(mode: mode, repository: container.workspacesRepository))
        _pathSuggestions = State(initialValue: PathSuggestionsModel(repository: container.pathSuggestionRepository))
        self.onOpenGit = onOpenGit
        self.onCreated = onCreated
        self.agentAPI = container.agentAPI
    }

    var body: some View {
        @Bindable var model = model
        NavigationStack {
            List {
                detailsSection(model)
                flagsSection(model)
                if model.showsFilterSection {
                    filterSection(model)
                }
                if let editing = model.editing, editing.is_git {
                    Section {
                        Button {
                            onOpenGit(editing.name)
                        } label: {
                            Label("Manage Git worktrees", systemImage: "arrow.triangle.branch")
                        }
                    }
                    .pixelFormSection()
                }
                if let submitError = model.submitError {
                    Section {
                        PixelErrorBanner(submitError)
                    }
                    .listRowBackground(Color.clear)
                }
            }
            .pixelForm()
            .navigationTitle(model.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(model.submitting ? "Saving…" : "Save") {
                        Task {
                            if await model.save() {
                                if let created = model.createdWorkspace { onCreated(created) }
                                dismiss()
                            }
                        }
                    }
                    .disabled(model.submitting)
                }
            }
            .interactiveDismissDisabled(model.isDirty)
            .toast($toast)
            .task {
                let version = try? await agentAPI.getAgentVersion()
                model.setAutoScanFilterInForm(version.map { $0.autoScanCapability == nil })
                await model.loadMetadataIfNeeded()
            }
            .onAppear { pathSuggestions.probe() }
            .onChange(of: model.message) { _, newValue in
                if let newValue {
                    toast = ToastMessage(text: newValue)
                    model.message = nil
                }
            }
            .onChange(of: model.name) { _, _ in
                if model.nameServerError != nil { model.nameServerError = nil }
            }
        }
        .pixelSheet()
        .presentationDetents([.medium, .large])
    }

    // MARK: - Sections

    @ViewBuilder
    private func detailsSection(_ model: WorkspaceFormModel) -> some View {
        @Bindable var model = model
        Section {
            fieldWithError(error: model.submitAttempted ? model.nameError : nil) {
                TextField("Name", text: $model.name)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .pixelField(isInvalid: model.submitAttempted && model.nameError != nil)
            }
            fieldWithError(error: model.submitAttempted ? model.pathError : nil) {
                TextField("Path", text: $model.path)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .pixelField(isInvalid: model.submitAttempted && model.pathError != nil)
                    .focused($pathFocused)
                    .onChange(of: pathFocused) { _, focused in pathSuggestions.focusChanged(focused) }
                    .onChange(of: model.path, initial: true) { _, text in pathSuggestions.textChanged(text) }
                PathSuggestionList(model: pathSuggestions) { name in
                    model.path = pathSuggestions.accept(name)
                }
                if model.path.isEmpty {
                    Text(pathBrowseHint(windowsPaths: pathSuggestions.windowsPaths))
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                }
            }
            TextField("Conda env (optional)", text: $model.condaEnv)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .pixelField()
            linearKeyField(model)
        } header: {
            PixelSectionHeader("Workspace")
        }
        .pixelFormSection()
    }

    @ViewBuilder
    private func linearKeyField(_ model: WorkspaceFormModel) -> some View {
        @Bindable var model = model
        VStack(alignment: .leading, spacing: 6) {
            SecretTextField(
                text: $model.linearApiKey,
                label: "Linear API key",
                revealed: $revealKey,
                placeholder: linearKeyPlaceholder(model)
            )
            .onChange(of: model.linearApiKey) { _, newValue in
                // Typing a new key cancels a pending clear (Android parity).
                if !newValue.isEmpty { model.clearLinearKey = false }
            }
            if model.isEditing {
                Button(model.clearLinearKey ? "Will clear on save" : "Clear key") {
                    model.clearLinearKey = true
                    model.linearApiKey = ""
                }
                .font(.caption)
                .disabled(model.clearLinearKey)
            }
        }
    }

    private func linearKeyPlaceholder(_ model: WorkspaceFormModel) -> String {
        if model.isEditing && model.clearLinearKey { return "Will be cleared on save" }
        if model.isEditing { return "Leave blank to keep current" }
        return "Optional"
    }

    @ViewBuilder
    private func flagsSection(_ model: WorkspaceFormModel) -> some View {
        @Bindable var model = model
        Section {
            ForEach(Array(model.flags.enumerated()), id: \.element.id) { pair in
                FlagRow(
                    flag: $model.flags[pair.offset],
                    keyError: model.submitAttempted ? model.flagKeyError(at: pair.offset) : nil,
                    valueError: model.submitAttempted ? model.flagValueError(at: pair.offset) : nil,
                    onRemove: { model.removeFlag(pair.element.id) }
                )
            }
            Button {
                model.addFlag()
            } label: {
                Label("Add flag", systemImage: "plus")
            }
        } header: {
            PixelSectionHeader("Default flags")
        }
        .pixelFormSection()
    }

    @ViewBuilder
    private func filterSection(_ model: WorkspaceFormModel) -> some View {
        @Bindable var model = model
        Section {
            LinearScanFilterEditor(
                triggerLabels: $model.triggerLabels,
                triggerStates: $model.triggerStates,
                activeState: $model.activeState,
                doneState: $model.doneState,
                removalLabel: $model.removalLabel,
                labelOptions: model.labelOptions,
                stateOptions: model.stateOptions,
                loading: model.metadataLoading,
                error: model.metadataError,
                showsErrors: model.submitAttempted,
                onRetry: { Task { await model.refreshMetadata() } }
            )
        } header: {
            HStack {
                PixelSectionHeader("Auto-scan filter")
                Spacer()
                Button {
                    Task { await model.refreshMetadata() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .foregroundStyle(colors.accent)
                }
            }
        }
        .pixelFormSection()
    }

    // MARK: - Helpers

    @ViewBuilder
    private func fieldWithError(error: String?, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            content()
            if let error {
                Text(error).font(.caption).foregroundStyle(colors.danger)
            }
        }
    }
}

private struct FlagRow: View {
    @Binding var flag: FlagEntry
    let keyError: String?
    let valueError: String?
    let onRemove: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                TextField("Key", text: $flag.key)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .pixelField(isInvalid: keyError != nil)
                Picker("", selection: $flag.kind) {
                    ForEach(FlagEntry.Kind.allCases) { kind in
                        Text(kind.label).tag(kind)
                    }
                }
                .labelsHidden()
                Button(role: .destructive, action: onRemove) {
                    Image(systemName: "xmark.square.fill")
                        .foregroundStyle(colors.danger)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Remove flag")
            }
            valueInput
            if let keyError {
                Text(keyError).font(.caption2).foregroundStyle(colors.danger)
            }
            if let valueError {
                Text(valueError).font(.caption2).foregroundStyle(colors.danger)
            }
        }
    }

    @ViewBuilder
    private var valueInput: some View {
        switch flag.kind {
        case .boolean:
            Toggle("Value", isOn: Binding(
                get: { flag.textValue == "true" },
                set: { flag.textValue = $0 ? "true" : "false" }
            ))
        case .number:
            TextField("Value", text: $flag.textValue)
                .keyboardType(.numbersAndPunctuation)
                .pixelField(isInvalid: valueError != nil)
        case .string:
            TextField("Value", text: $flag.textValue)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .pixelField(isInvalid: valueError != nil)
        }
    }
}
