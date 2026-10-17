import SwiftUI

/// Create/edit project form (Android `ProjectFormSheet`). Presented as a
/// medium/large sheet; blocks interactive dismissal while dirty so edits are
/// not lost by an accidental swipe-down.
struct ProjectFormSheet: View {
    let mode: ProjectFormMode
    let container: AppContainer
    var onOpenGit: (String) -> Void = { _ in }
    var onCreated: (ProjectInfo) -> Void = { _ in }

    var body: some View {
        ProjectFormContent(mode: mode, container: container, onOpenGit: onOpenGit, onCreated: onCreated)
    }
}

private struct ProjectFormContent: View {
    @State private var model: ProjectFormModel
    @State private var revealKey = false
    @State private var toast: ToastMessage?
    @State private var pathSuggestions: PathSuggestionsModel
    @FocusState private var pathFocused: Bool

    @Environment(\.dismiss) private var dismiss
    @Environment(\.mobaideColors) private var colors

    private let onOpenGit: (String) -> Void
    private let onCreated: (ProjectInfo) -> Void

    init(
        mode: ProjectFormMode,
        container: AppContainer,
        onOpenGit: @escaping (String) -> Void,
        onCreated: @escaping (ProjectInfo) -> Void
    ) {
        _model = State(initialValue: ProjectFormModel(mode: mode, repository: container.projectsRepository))
        _pathSuggestions = State(initialValue: PathSuggestionsModel(repository: container.pathSuggestionRepository))
        self.onOpenGit = onOpenGit
        self.onCreated = onCreated
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
                                if let created = model.createdProject { onCreated(created) }
                                dismiss()
                            }
                        }
                    }
                    .disabled(model.submitting)
                }
            }
            .interactiveDismissDisabled(model.isDirty)
            .toast($toast)
            .task { await model.loadMetadataIfNeeded() }
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
    private func detailsSection(_ model: ProjectFormModel) -> some View {
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
                    Text(pathBrowseHint).font(.caption).foregroundStyle(colors.textMuted)
                }
            }
            TextField("Conda env (optional)", text: $model.condaEnv)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .pixelField()
            linearKeyField(model)
        } header: {
            PixelSectionHeader("Project")
        }
        .pixelFormSection()
    }

    @ViewBuilder
    private func linearKeyField(_ model: ProjectFormModel) -> some View {
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

    private func linearKeyPlaceholder(_ model: ProjectFormModel) -> String {
        if model.isEditing && model.clearLinearKey { return "Will be cleared on save" }
        if model.isEditing { return "Leave blank to keep current" }
        return "Optional"
    }

    @ViewBuilder
    private func flagsSection(_ model: ProjectFormModel) -> some View {
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
    private func filterSection(_ model: ProjectFormModel) -> some View {
        @Bindable var model = model
        Section {
            if model.metadataLoading {
                PixelLoadingBlock()
            }
            if let error = model.metadataError {
                HStack {
                    Text(error).font(.caption).foregroundStyle(colors.danger)
                    Spacer()
                    Button("Retry") { Task { await model.refreshMetadata() } }
                        .buttonStyle(.pixelSmall)
                }
            }

            chipGroup(title: "Trigger labels", error: model.submitAttempted ? triggerLabelsError(model) : nil) {
                ForEach(model.labelOptions, id: \.self) { label in
                    SelectableChip(text: label, isSelected: model.triggerLabels.contains(label)) {
                        toggle(&model.triggerLabels, label)
                    }
                }
            }
            chipGroup(title: "Trigger states", error: model.submitAttempted ? triggerStatesError(model) : nil) {
                ForEach(model.stateOptions, id: \.name) { state in
                    SelectableChip(
                        text: state.name,
                        glyph: stateGlyph(state.type),
                        isSelected: model.triggerStates.contains(state.name)
                    ) {
                        toggle(&model.triggerStates, state.name)
                    }
                }
            }

            singleSelect("Active state", selection: $model.activeState, options: model.stateOptions.map(\.name))
            singleSelect("Done state", selection: $model.doneState, options: model.stateOptions.map(\.name))
            singleSelect("Removal label", selection: $model.removalLabel, options: model.labelOptions)
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

    private func triggerLabelsError(_ model: ProjectFormModel) -> String? {
        model.triggerLabels.isEmpty ? "Select at least one trigger label" : nil
    }

    private func triggerStatesError(_ model: ProjectFormModel) -> String? {
        model.triggerStates.isEmpty ? "Select at least one trigger state" : nil
    }

    @ViewBuilder
    private func fieldWithError(error: String?, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            content()
            if let error {
                Text(error).font(.caption).foregroundStyle(colors.danger)
            }
        }
    }

    @ViewBuilder
    private func chipGroup(title: String, error: String?, @ViewBuilder chips: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).sectionHeading()
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) { chips() }
            }
            if let error {
                Text(error).font(.caption).foregroundStyle(colors.danger)
            }
        }
    }

    private func singleSelect(_ label: String, selection: Binding<String>, options: [String]) -> some View {
        Menu {
            ForEach(options, id: \.self) { option in
                Button(option) { selection.wrappedValue = option }
            }
        } label: {
            LabeledContent(label, value: selection.wrappedValue.isEmpty ? "Select" : selection.wrappedValue)
        }
    }

    private func toggle(_ set: inout Set<String>, _ value: String) {
        if set.contains(value) { set.remove(value) } else { set.insert(value) }
    }

    private func stateGlyph(_ type: String) -> String {
        switch type {
        case "backlog": return "◴"
        case "unstarted": return "○"
        case "started": return "◐"
        case "completed": return "●"
        case "canceled": return "⊘"
        case "triage": return "△"
        default: return "·"
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

private struct SelectableChip: View {
    let text: String
    var glyph: String?
    let isSelected: Bool
    let onTap: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Button(action: onTap) {
            PixelChip(glyph.map { "\($0) \(text)" } ?? text, .verbatim(checked: isSelected))
                .padding(.bottom, 2)
        }
        .buttonStyle(.plain)
        .transaction { $0.animation = nil }
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
    }
}
