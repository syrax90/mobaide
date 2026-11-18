import SwiftUI

/// The Auto Scan step's issue-source sections for an agent with
/// `capabilities.autoscan`: the source choice, then the GitHub status and
/// filter, or the Linear key and filter.
struct AutoScanSourceSections: View {
    @Bindable var setup: AutoScanSetupModel
    let setupStarting: Bool
    let onSetup: (GitHubSetupAction) -> Void

    @State private var revealLinearKey = false
    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Section {
            ForEach(setup.sources, id: \.self) { source in
                SelectableRow(
                    title: source.title,
                    subtitle: source.subtitle,
                    selected: setup.source == source
                ) {
                    setup.source = source
                }
            }
        } header: {
            PixelSectionHeader("Issue source")
        }
        .pixelFormSection()

        switch setup.source {
        case .github?:
            githubStatusSection
            githubFilterSection
        case .linear?:
            if setup.hasLinearKey {
                linearFilterSection
            } else {
                linearKeySection
            }
        case nil:
            EmptyView()
        }
    }

    // MARK: - GitHub

    @ViewBuilder
    private var githubStatusSection: some View {
        Section {
            switch setup.githubStatus {
            case .idle, .loading:
                HStack(spacing: 10) {
                    PixelLoadingBlock(width: 7, height: 13)
                    Text("Checking GitHub on the PC…")
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                }
            case .failed(let message):
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(colors.danger)
            case .loaded(let status):
                githubStatusRows(status)
            }
            checkAgainRow
        } header: {
            PixelSectionHeader("GitHub")
        }
        .pixelFormSection()
    }

    @ViewBuilder
    private func githubStatusRows(_ status: GitHubStatusResponse) -> some View {
        if !status.installed {
            Text(status.problem ?? "GitHub CLI (gh) is not installed on the PC.")
                .font(.footnote)
                .foregroundStyle(colors.warning)
            if status.can_install, setup.canRunGitHubSetup {
                VStack(alignment: .leading, spacing: 6) {
                    setupButton("Install GitHub CLI", action: .install)
                    if let command = status.install_command {
                        Text("Runs \(command) in a terminal on the PC.")
                            .font(.caption)
                            .foregroundStyle(colors.textMuted)
                    }
                }
            } else {
                Text("Install it from cli.github.com, then tap Check again.")
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
            }
        } else if let problem = status.problem {
            Text(problem)
                .font(.footnote)
                .foregroundStyle(colors.warning)
            if !status.signed_in, status.host?.contains("github") == true {
                if setup.canRunGitHubSetup {
                    setupButton("Sign in to GitHub", action: .signIn)
                } else {
                    Text("Run gh auth login on the PC, then tap Check again.")
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                }
            }
        } else {
            Text(readyText(status))
                .font(.footnote)
                .foregroundStyle(colors.text)
        }
    }

    private func readyText(_ status: GitHubStatusResponse) -> String {
        var parts: [String] = []
        if let repo = status.repo { parts.append("Repository \(repo)") }
        if let account = status.account { parts.append("Signed in as \(account)") }
        return parts.isEmpty ? "GitHub is ready." : parts.joined(separator: " · ")
    }

    private func setupButton(_ title: String, action: GitHubSetupAction) -> some View {
        Button {
            onSetup(action)
        } label: {
            HStack(spacing: 8) {
                Text(title)
                if setupStarting {
                    PixelLoadingBlock(width: 7, height: 13)
                }
            }
        }
        .buttonStyle(.pixelSmallPrimary)
        .disabled(setupStarting)
    }

    private var checkAgainRow: some View {
        HStack(spacing: 10) {
            Button("Check again") {
                Task { await setup.refreshGitHubStatus() }
            }
            .buttonStyle(.pixelSmall)
            .disabled(setup.githubChecking)
            if setup.githubChecking, case .loaded = setup.githubStatus {
                PixelLoadingBlock(width: 7, height: 13)
            }
            Spacer()
        }
    }

    @ViewBuilder
    private var githubFilterSection: some View {
        Section {
            labelsField
            VStack(alignment: .leading, spacing: 6) {
                Text("Assigned to").sectionHeading()
                TextField("Anyone", text: $setup.githubAssignee)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .pixelField()
                Text("Empty = any assignee. @me = the signed-in GitHub account.")
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("Label to add when work starts").sectionHeading()
                HStack(spacing: 8) {
                    TextField("None", text: $setup.githubActiveLabel)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .pixelField()
                    if !setup.githubRepoLabels.isEmpty {
                        Menu {
                            ForEach(setup.githubRepoLabels, id: \.name) { label in
                                Button(label.name) { setup.githubActiveLabel = label.name }
                            }
                        } label: {
                            Image(systemName: "chevron.up.chevron.down")
                                .foregroundStyle(colors.accent)
                                .frame(width: 44, height: 44)
                                .contentShape(Rectangle())
                        }
                        .accessibilityLabel("Choose a repository label")
                    }
                }
                Text("Optional, for example in-progress. It must exist in the repository.")
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
            }
        } header: {
            PixelSectionHeader("GitHub filter")
        } footer: {
            PixelSectionFooter("When an issue is closed, its worktree is removed.")
        }
        .pixelFormSection()
    }

    private var labelsField: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Labels to pick up").sectionHeading()
            if !setup.githubLabels.isEmpty {
                ChipFlowLayout(horizontalSpacing: 6, verticalSpacing: 6) {
                    ForEach(setup.githubLabels, id: \.self) { label in
                        Button {
                            setup.removeGitHubLabel(label)
                        } label: {
                            PixelChip("\(label)  ✕", .verbatim(checked: true))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Remove label \(label)")
                    }
                }
            }
            HStack(spacing: 8) {
                TextField("Add a label", text: $setup.githubLabelDraft)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .submitLabel(.done)
                    .onSubmit { setup.submitGitHubLabelDraft() }
                    .pixelField(isInvalid: setup.githubLabelError != nil)
                Button("Add") { setup.submitGitHubLabelDraft() }
                    .buttonStyle(.pixelSmall)
                    .disabled(setup.githubLabelDraft.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if let error = setup.githubLabelError {
                Text(error).font(.caption).foregroundStyle(colors.danger)
            }
            let suggestions = setup.githubLabelSuggestions
            if !suggestions.isEmpty {
                ChipFlowLayout(horizontalSpacing: 6, verticalSpacing: 6) {
                    ForEach(suggestions, id: \.name) { label in
                        Button {
                            setup.addGitHubLabel(label.name)
                        } label: {
                            PixelChip("+ \(label.name)", .verbatim(checked: false))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Add label \(label.name)")
                    }
                }
            }
            let hidden = setup.githubHiddenLabelSuggestionCount
            if hidden > 0 {
                Text("\(hidden) more \(hidden == 1 ? "label" : "labels"): type to find them.")
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
            }
            if let error = setup.githubRepoLabelsError {
                Text(error).font(.caption).foregroundStyle(colors.warning)
            }
            Text("An open issue with any of these labels is picked up. The scan removes the label when it starts, so each issue runs once.")
                .font(.caption)
                .foregroundStyle(colors.textMuted)
        }
    }

    // MARK: - Linear

    @ViewBuilder
    private var linearKeySection: some View {
        Section {
            SecretTextField(
                text: $setup.linearKeyDraft,
                label: "Linear API key",
                revealed: $revealLinearKey,
                isInvalid: setup.linearKeyError != nil
            )
            HStack(spacing: 10) {
                Button {
                    Task { await setup.saveLinearKey() }
                } label: {
                    HStack(spacing: 8) {
                        Text("Save key")
                        if setup.savingLinearKey {
                            PixelLoadingBlock(width: 7, height: 13)
                        }
                    }
                }
                .buttonStyle(.pixelSmallPrimary)
                .disabled(setup.savingLinearKey || setup.linearKeyDraft.trimmingCharacters(in: .whitespaces).isEmpty)
                Spacer()
            }
            if let error = setup.linearKeyError {
                Text(error).font(.caption).foregroundStyle(colors.danger)
            }
        } header: {
            PixelSectionHeader("Linear")
        } footer: {
            PixelSectionFooter("This workspace has no Linear API key. The key is saved on the PC with the workspace.")
        }
        .pixelFormSection()
    }

    @ViewBuilder
    private var linearFilterSection: some View {
        Section {
            LinearScanFilterEditor(
                triggerLabels: $setup.linearTriggerLabels,
                triggerStates: $setup.linearTriggerStates,
                activeState: $setup.linearActiveState,
                doneState: $setup.linearDoneState,
                removalLabel: $setup.linearRemovalLabel,
                labelOptions: setup.linearLabelOptions,
                stateOptions: setup.linearStateOptions,
                loading: setup.linearMetadataLoading,
                error: setup.linearMetadataError,
                onRetry: { Task { await setup.loadLinearMetadata(force: true) } }
            )
        } header: {
            HStack {
                PixelSectionHeader("Linear filter")
                Spacer()
                Button {
                    Task { await setup.loadLinearMetadata(force: true) }
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .foregroundStyle(colors.accent)
                }
                .accessibilityLabel("Reload Linear labels and states")
            }
        }
        .pixelFormSection()
    }
}

/// The Auto Scan step's agent choice: the kinds `capabilities.autoscan`
/// lists; one the PC does not have is shown but cannot be picked.
struct AutoScanAgentSection: View {
    @Bindable var setup: AutoScanSetupModel

    var body: some View {
        Section {
            ForEach(setup.agents, id: \.self) { kind in
                let available = setup.isAgentAvailable(kind)
                SelectableRow(
                    title: AutoScanDisplay.agentName(kind),
                    subtitle: available ? nil : "Not installed on the PC",
                    selected: setup.cli == kind
                ) {
                    setup.cli = kind
                }
                .disabled(!available)
            }
        } header: {
            PixelSectionHeader("AI agent")
        }
        .pixelFormSection()
    }
}

/// A form row with a title, an optional subtitle and a radio square.
struct SelectableRow: View {
    let title: String
    var subtitle: String?
    let selected: Bool
    let action: () -> Void

    @Environment(\.mobaideColors) private var colors
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).foregroundStyle(isEnabled ? colors.text : colors.gray)
                    if let subtitle {
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(isEnabled ? colors.textMuted : colors.warning)
                    }
                }
                Spacer()
                PixelRadio(isSelected: selected)
            }
        }
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}
