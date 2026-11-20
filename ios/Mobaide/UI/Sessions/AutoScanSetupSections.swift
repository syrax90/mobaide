import SwiftUI

// The Auto Scan step for an agent with `capabilities.autoscan`, top to bottom:
// intro, templates, "How Auto Scan works", then four numbered steps (where to
// find issues, which issues, AI agent, instructions). The agent options and
// the instructions live in `CreateSessionScreen`, which owns their buckets.

/// "1  Where to find issues": a numbered step heading.
struct AutoScanStepHeader: View {
    let number: Int
    let title: String

    /// Scaled: the ordinal is Dynamic Type text (`PixelNumberedStep`).
    @ScaledMetric(relativeTo: .caption) private var side: CGFloat = 20

    init(_ number: Int, _ title: String) {
        self.number = number
        self.title = title
    }

    var body: some View {
        HStack(spacing: 8) {
            Text("\(number)")
                .chromeText()
                .foregroundStyle(.white)
                .frame(minWidth: side, minHeight: side)
                .background(MobaideColorScheme.brand)
                .pixelRing(MobaideColorScheme.brand2)
            Text(title).sectionHeading()
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

/// A tinted, ringed note that fills its list row: `info` explains, `attention`
/// says what matters most, `tip` suggests.
struct AutoScanCallout: View {
    enum Kind {
        case info, attention, tip
    }

    let kind: Kind
    var title: String?
    let lines: [String]
    /// Lines shown as a bulleted list.
    var bulleted = false

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.body.weight(.semibold))
                .foregroundStyle(tint)
                .frame(width: 22)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                if let title {
                    Text(title)
                        .chromeText()
                        .foregroundStyle(tint)
                }
                ForEach(lines, id: \.self) { line in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        if bulleted {
                            Text("•").font(.footnote).foregroundStyle(tint)
                        }
                        Text(line)
                            .font(.footnote)
                            .foregroundStyle(kind == .tip ? colors.textMuted : colors.text)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(PixelMetrics.cardInsets)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(kind == .tip ? colors.bg2 : colors.surface)
        .pixelRing(kind == .tip ? colors.rule : tint)
        .listRowInsets(EdgeInsets())
        .accessibilityElement(children: .combine)
    }

    private var icon: String {
        switch kind {
        case .info: return "info.square.fill"
        case .attention: return "exclamationmark.square.fill"
        case .tip: return "lightbulb"
        }
    }

    private var tint: Color {
        switch kind {
        case .info: return colors.accent
        case .attention: return colors.warning
        case .tip: return colors.textMuted
        }
    }
}

struct AutoScanIntroSection: View {
    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Section {
            VStack(alignment: .leading, spacing: 6) {
                Text("Auto Scan").pageTitle()
                Text("An AI agent picks up matching issues one by one and works on each in its own session.")
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.vertical, 4)
        }
    }
}

/// The template picker: apply a saved set of choices, delete one, or save the
/// current choices. Errors stay here and never block Start.
struct AutoScanTemplatesSection: View {
    @Bindable var setup: AutoScanSetupModel
    let onSave: () -> Void
    let onDelete: (AutoScanTemplateDto) -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Section {
            HStack(spacing: 12) {
                Text("Template")
                Spacer(minLength: 8)
                Menu {
                    Section {
                        ForEach(setup.templates) { template in
                            Button {
                                setup.applyTemplate(template)
                            } label: {
                                Text(template.name)
                                Text(AutoScanSetupModel.templateSummary(template))
                            }
                        }
                    }
                    Menu("Delete a template") {
                        ForEach(setup.templates) { template in
                            Button(template.name, role: .destructive) { onDelete(template) }
                                .disabled(setup.deletingTemplateIds.contains(template.id))
                        }
                    }
                } label: {
                    HStack(spacing: 6) {
                        Text(setup.appliedTemplateName ?? "Choose a template")
                            .lineLimit(1)
                        Image(systemName: "chevron.up.chevron.down")
                            .font(.caption)
                    }
                    .foregroundStyle(setup.templates.isEmpty ? colors.gray : colors.accent)
                }
                .disabled(setup.templates.isEmpty)
            }
            statusRows
            Button("Save as template…", action: onSave)
                .buttonStyle(.pixelSmall)
                .disabled(!setup.canSaveTemplate)
        } header: {
            PixelSectionHeader("Templates")
        }
    }

    @ViewBuilder
    private var statusRows: some View {
        switch setup.templatesState {
        case .idle, .loading:
            HStack(spacing: 10) {
                PixelLoadingBlock(width: 7, height: 13)
                Text("Loading templates…")
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
            }
        case .failed(let message):
            HStack(spacing: 10) {
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(colors.danger)
                Spacer()
                Button("Retry") { Task { await setup.loadTemplates(force: true) } }
                    .buttonStyle(.pixelSmall)
            }
        case .loaded:
            if setup.templates.isEmpty {
                Text("No templates yet. Set up a scan, then save it as a template.")
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
            }
        }
        if let notice = setup.templateNotice {
            Text(notice)
                .font(.footnote)
                .foregroundStyle(colors.ok)
        }
        if let error = setup.templateActionError {
            Text(error)
                .font(.footnote)
                .foregroundStyle(colors.danger)
        }
    }
}

/// Where each issue runs and when its worktree goes, said once for the step.
struct AutoScanHowItWorksSection: View {
    let setup: AutoScanSetupModel

    var body: some View {
        Section {
            AutoScanCallout(kind: .info, title: "How Auto Scan works", lines: lines)
        }
    }

    private var lines: [String] {
        let isGit = setup.workspace?.is_git != false
        var lines = [
            isGit
                ? "Each issue runs in its own git worktree and branch, so the agent's work never touches your working copy."
                : "This folder is not a git repository, so each issue runs in the folder itself.",
            "Each session starts at once with the issue as its prompt. You get a notification when it needs you.",
        ]
        guard isGit else { return lines }
        lines.append("Auto Scan trusts each worktree it creates, so the agent does not ask to trust the folder.")
        switch setup.source {
        case .github?:
            lines.append("When an issue is closed, its worktree is removed. A worktree with uncommitted changes is kept.")
        case .linear?:
            let done = setup.linearDoneState.trimmingCharacters(in: .whitespacesAndNewlines)
            lines.append("When an issue moves to “\(done.isEmpty ? "Done" : done)”, its worktree is removed.")
        case nil:
            break
        }
        return lines
    }
}

/// Steps 1 and 2: the source choice, then the GitHub status and filter, or the
/// Linear key and filter.
struct AutoScanSourceSections: View {
    @Bindable var setup: AutoScanSetupModel
    let setupStarting: Bool
    /// Kept on the device (`PreferencesStore`): hidden tips stay hidden.
    @Binding var tipsHidden: Bool
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
            AutoScanStepHeader(1, "Where to find issues")
        }

        switch setup.source {
        case .github?:
            githubStatusSection
            githubPickUpSections
            githubMoreFilterSection
        case .linear?:
            if setup.hasLinearKey {
                linearFilterSection
            } else {
                linearKeySection
            }
        case nil:
            Section {
                Text("Choose where to find issues first.")
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
            } header: {
                AutoScanStepHeader(2, "Which issues")
            }
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
                statusLine(message, ok: false, tone: colors.danger)
            case .loaded(let status):
                githubStatusRows(status)
            }
            checkAgainRow
        } header: {
            AutoScanStepHeader(2, "Which issues")
        }
    }

    private func statusLine(_ text: String, ok: Bool, tone: Color) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: ok ? "checkmark.square.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(tone)
                .accessibilityHidden(true)
            Text(text)
                .font(.footnote)
                .foregroundStyle(ok ? colors.text : tone)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private func githubStatusRows(_ status: GitHubStatusResponse) -> some View {
        if !status.installed {
            statusLine(status.problem ?? "GitHub CLI (gh) is not installed on the PC.", ok: false, tone: colors.warning)
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
            statusLine(problem, ok: false, tone: colors.warning)
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
            statusLine(readyText(status), ok: true, tone: colors.ok)
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
                Task { await setup.refreshGitHubStatus(userAsked: true) }
            }
            .buttonStyle(.pixelSmall)
            .disabled(setup.githubChecking)
            if setup.githubChecking, case .loaded = setup.githubStatus {
                PixelLoadingBlock(width: 7, height: 13)
            }
            Spacer()
        }
    }

    /// The pick-up rule, a tip, the trigger labels and the issue types.
    @ViewBuilder
    private var githubPickUpSections: some View {
        Section {
            AutoScanCallout(
                kind: .attention,
                title: "How issues are picked up",
                lines: [
                    "An open issue with any of these labels is picked up. When work starts, the scan removes the label, so each issue runs only once. Add the label again to run the issue again.",
                ]
            )
        }
        Section {
            if tipsHidden {
                tipsToggle("Show tips", hidden: false)
            } else {
                AutoScanCallout(
                    kind: .tip,
                    title: "Tips",
                    lines: [
                        "Create a dedicated label, for example auto-scan, and add it only to the issues an agent should solve.",
                        "Avoid common labels such as bug: every open issue with them would start a session.",
                        "Keep the label out of issue templates and workflows that label new issues, or anyone who opens an issue could start a session on your PC.",
                        "Use one label per kind of work, such as auto-scan-small and auto-scan-complex, and save a template for each.",
                    ],
                    bulleted: true
                )
                tipsToggle("Hide tips", hidden: true)
            }
        }
        Section {
            labelsField
            issueTypesField
        }
    }

    /// A text button that hides or shows the tips.
    private func tipsToggle(_ title: String, hidden: Bool) -> some View {
        Button(title) { tipsHidden = hidden }
            .font(.footnote.weight(.semibold))
            .foregroundStyle(colors.accent)
            .buttonStyle(.borderless)
            .frame(minHeight: 44)
    }

    @ViewBuilder
    private var githubMoreFilterSection: some View {
        Section {
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
        }
    }

    /// The chosen labels (also ones the repository does not list, so they can
    /// be unchosen), then the repository's other labels to pick from.
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
            if setup.githubLabelSearchShown {
                TextField("Search the repository's labels", text: $setup.githubLabelSearch)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .submitLabel(.search)
                    .pixelField()
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
                        .disabled(setup.githubLabels.count >= AutoScanSetupModel.maxGitHubLabels)
                        .accessibilityLabel("Pick up label \(label.name)")
                    }
                }
            }
            let hidden = setup.githubHiddenLabelSuggestionCount
            if hidden > 0 {
                Text("\(hidden) more \(hidden == 1 ? "label" : "labels"): search to find them.")
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
            }
            if setup.githubLabelsLoaded, setup.githubRepoLabels.isEmpty {
                Text("This repository has no labels yet. Create one on GitHub, then tap Check again.")
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
            }
            if setup.githubLabels.count >= AutoScanSetupModel.maxGitHubLabels {
                Text("You can pick up at most \(AutoScanSetupModel.maxGitHubLabels) labels.")
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
            }
            if let error = setup.githubLabelError {
                Text(error).font(.caption).foregroundStyle(colors.danger)
            }
            if let error = setup.githubRepoLabelsError {
                HStack(spacing: 10) {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(colors.warning)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 8)
                    Button("Try again") { Task { await setup.loadGitHubLabels() } }
                        .buttonStyle(.pixelSmall)
                }
            }
        }
        .padding(.vertical, 4)
    }

    private var issueTypesField: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Issue types").sectionHeading()
            Text("Only issues of the chosen types are picked up. None chosen: any type.")
                .font(.caption)
                .foregroundStyle(colors.textMuted)
            let options = setup.githubIssueTypeOptions
            if !options.isEmpty {
                ChipFlowLayout(horizontalSpacing: 6, verticalSpacing: 6) {
                    ForEach(options, id: \.self) { name in
                        let chosen = setup.isGitHubIssueTypeChosen(name)
                        Button {
                            setup.toggleGitHubIssueType(name)
                        } label: {
                            PixelChip(chosen ? "\(name)  ✓" : name, .verbatim(checked: chosen))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(name)
                        .accessibilityAddTraits(chosen ? .isSelected : [])
                    }
                }
            }
            if setup.githubLabelsLoaded, setup.githubRepoIssueTypes.isEmpty {
                Text("This repository has no issue types.")
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
            }
        }
        .padding(.vertical, 4)
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
            AutoScanStepHeader(2, "Which issues")
        } footer: {
            PixelSectionFooter("This workspace has no Linear API key. The key is saved on the PC with the workspace.")
        }
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
                AutoScanStepHeader(2, "Which issues")
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
    }
}

/// Step 3's agent choice: the kinds `capabilities.autoscan` lists; one the PC
/// does not have is shown but cannot be picked.
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
            AutoScanStepHeader(3, "AI agent")
        }
    }
}

/// How each issue's session opens: the terminal, or the chat view for an agent
/// `capabilities.chat` lists.
struct AutoScanSessionViewSection: View {
    @Bindable var setup: AutoScanSetupModel

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        if let cli = setup.cli {
            let view = setup.effectiveSessionView
            SessionViewChooserSection(
                chatSelected: view == .chat,
                chatUnavailableReason: setup.chatUnavailableReason(cli),
                onChoose: { setup.sessionView = $0 ? .chat : .terminal }
            ) {
                if setup.isChatAvailable(cli), cli == "junie", view == .terminal {
                    Text("Junie asks to trust a new worktree unless you trusted the workspace folder in Junie. Trust it once in Junie, or use Chat.")
                        .font(.footnote)
                        .foregroundStyle(colors.warning)
                }
            }
        }
    }
}

/// "Time between scans": a menu row (the whole row opens the menu, like the
/// Model and Effort rows), then its hint as a muted row.
struct AutoScanIntervalRows: View {
    @Bindable var setup: AutoScanSetupModel

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Picker("Time between scans", selection: $setup.intervalMinutes) {
            ForEach(setup.intervalOptions, id: \.self) { minutes in
                Text(AutoScanSetupModel.intervalTitle(minutes)).tag(minutes)
            }
        }
        .pickerStyle(.menu)
        Text("After each scan, Auto Scan waits this long before it looks for new issues. Run now starts a scan at once.")
            .font(.caption)
            .foregroundStyle(colors.textMuted)
    }
}

/// "Save as template…": the name, and whether the save replaces a template.
struct AutoScanSaveTemplateSheet: View {
    let setup: AutoScanSetupModel
    let onSave: (String) async -> Bool

    @State private var name: String
    @FocusState private var focused: Bool
    @Environment(\.dismiss) private var dismiss
    @Environment(\.mobaideColors) private var colors

    init(setup: AutoScanSetupModel, onSave: @escaping (String) async -> Bool) {
        self.setup = setup
        self.onSave = onSave
        _name = State(initialValue: setup.appliedTemplateName ?? "")
    }

    var body: some View {
        let replacing = setup.existingTemplate(named: name)
        let lengthError = AutoScanSetupModel.normalizedTemplateName(name).unicodeScalars.count
            > AutoScanSetupModel.maxTemplateNameLength
            ? AutoScanSetupModel.templateNameError(name) : nil
        NavigationStack {
            List {
                Section {
                    TextField("Name", text: $name)
                        .focused($focused)
                        .submitLabel(.done)
                        .onSubmit { save() }
                        .pixelField(isInvalid: lengthError != nil)
                    if let lengthError {
                        Text(lengthError).font(.footnote).foregroundStyle(colors.danger)
                    } else if let replacing {
                        Text("Replaces the template “\(replacing.name)”.")
                            .font(.footnote)
                            .foregroundStyle(colors.warning)
                    } else {
                        Text("Saves the source, filter, agent, its options, the session view, the time between scans and the instructions on your PC.")
                            .font(.footnote)
                            .foregroundStyle(colors.textMuted)
                    }
                    if let error = setup.templateActionError {
                        Text(error).font(.footnote).foregroundStyle(colors.danger)
                    }
                } header: {
                    PixelSectionHeader("Template name")
                }
                .pixelFormSection()
            }
            .pixelForm()
            .navigationTitle("Save as template")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(replacing == nil ? "Save" : "Replace") { save() }
                        .disabled(AutoScanSetupModel.templateNameError(name) != nil || setup.savingTemplate)
                }
            }
        }
        .presentationDetents([.medium])
        .pixelSheet()
        .onAppear { focused = true }
    }

    private func save() {
        guard AutoScanSetupModel.templateNameError(name) == nil, !setup.savingTemplate else { return }
        let chosen = name
        Task {
            if await onSave(chosen) { dismiss() }
        }
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
