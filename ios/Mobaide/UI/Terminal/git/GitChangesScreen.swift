import SwiftUI

/// Session-scoped working-tree overview (Android `GitChangesScreen`), pushed
/// from the terminal's `⋯ → Git → Changes…` (or `Commit…`, `Fetch / Pull /
/// Push…` and `Rebase…`, which open their sheet over it).
struct GitChangesScreen: View {
    let sessionId: String
    var openCommit = false
    var openSync = false
    var openRebase = false
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            GitChangesContent(
                container: container,
                sessionId: sessionId,
                openCommit: openCommit,
                openSync: openSync,
                openRebase: openRebase
            )
        }
    }
}

private struct GitChangesContent: View {
    @State private var model: GitChangesModel
    /// Multi-select lives in SwiftUI's edit mode so the `List` draws the
    /// checkmarks; the model owns the ticked ids.
    @State private var editMode: EditMode = .inactive

    @Environment(Router.self) private var router
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.mobaideColors) private var colors

    init(container: AppContainer, sessionId: String, openCommit: Bool, openSync: Bool, openRebase: Bool) {
        let sessionRepository = container.sessionRepository
        _model = State(initialValue: GitChangesModel(
            sessionId: sessionId,
            agentAPI: container.agentAPI,
            agentEventBus: container.agentEventBus,
            projectName: {
                sessionRepository.currentSessions.first { $0.session_id == sessionId }?.project_name
            },
            openCommit: openCommit,
            openSync: openSync,
            openRebase: openRebase
        ))
    }

    var body: some View {
        content
            .environment(\.editMode, $editMode)
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbarContent }
            .refreshable { await model.load() }
            // Keyed on `.background` like the terminal's own gate: nobody reads
            // this screen from a backgrounded app, and `.inactive` (Control
            // Center, an app-switcher peek) never suspends the app. A Diff push
            // cancels this too, so the poll pauses under the child and the
            // return refreshes at once.
            .task(id: scenePhase != .background) {
                guard scenePhase != .background else { return }
                await model.run()
            }
            .task { await model.observeGitChanged() }
            .task { await model.observeGitJobFrames() }
            // Restarted whenever the followed job changes or finishes, so the
            // first poll of a job is immediate and a finished one costs nothing.
            .task(id: model.jobReconcileKey(active: scenePhase != .background)) {
                guard scenePhase != .background else { return }
                await model.runJobReconcile()
            }
            .task {
                await model.loadCapabilities()
                model.consumeOpenCommit()
                model.consumeOpenSync()
                model.consumeOpenRebase()
            }
            .confirmationDialog(
                model.discardPrompt?.title ?? "Discard?",
                isPresented: discardPresented,
                titleVisibility: .visible,
                presenting: model.discardPrompt
            ) { _ in
                Button("Discard", role: .destructive) {
                    Task {
                        await model.confirmDiscard()
                        endSelectionIfEmpty()
                    }
                }
                Button("Cancel", role: .cancel) { model.dismissDiscard() }
            } message: { prompt in
                Text(prompt.body)
            }
            .sheet(isPresented: commitPresented) {
                GitCommitSheet(model: model)
            }
            .sheet(isPresented: syncPresented) {
                GitSyncSheet(model: model)
            }
            .sheet(isPresented: rebasePresented) {
                GitRebaseSheet(model: model)
            }
            .confirmationDialog(
                model.rebasePrompt?.title ?? "Confirm?",
                isPresented: rebasePromptPresented,
                titleVisibility: .visible,
                presenting: model.rebasePrompt
            ) { prompt in
                Button(prompt.confirm, role: .destructive) {
                    Task { await model.confirmRebaseStep() }
                }
                Button("Cancel", role: .cancel) { model.dismissRebaseStep() }
            } message: { prompt in
                Text(prompt.body)
            }
            .toast($model.toast)
            .onChange(of: editMode) { _, newValue in
                if !newValue.isEditing { model.selection = [] }
            }
            .onChange(of: model.hasActionableRows) { _, newValue in
                if !newValue { editMode = .inactive }
            }
            .holdsSessionWatch(model.sessionId)
    }

    private var title: String {
        guard editMode.isEditing else { return "Changes" }
        return model.selection.isEmpty ? "Select files" : "\(model.selection.count) selected"
    }

    // Only the prompt: SwiftUI writes this false before the confirm action
    // runs, and the rows it acts on must survive that write.
    private var discardPresented: Binding<Bool> {
        Binding(
            get: { model.discardPrompt != nil },
            set: { if !$0 { model.discardPrompt = nil } }
        )
    }

    /// Selection mode ends with the last ticked row, as on Android; a partly
    /// applicable selection keeps it.
    private func endSelectionIfEmpty() {
        if editMode.isEditing, model.selection.isEmpty {
            withAnimation { editMode = .inactive }
        }
    }

    private var commitPresented: Binding<Bool> {
        Binding(
            get: { model.commitSheet != nil },
            set: { if !$0 { model.closeCommit() } }
        )
    }

    private var syncPresented: Binding<Bool> {
        Binding(
            get: { model.syncSheet != nil },
            set: { if !$0 { model.closeSync() } }
        )
    }

    private var rebasePresented: Binding<Bool> {
        Binding(
            get: { model.rebaseSheet != nil },
            set: { if !$0 { model.closeRebase() } }
        )
    }

    // Only the prompt: SwiftUI writes this false before the confirm action
    // runs, and the step it acts on must survive that write.
    private var rebasePromptPresented: Binding<Bool> {
        Binding(
            get: { model.rebasePrompt != nil },
            set: { if !$0 { model.rebasePrompt = nil } }
        )
    }

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItemGroup(placement: .topBarTrailing) {
            if editMode.isEditing {
                Button("Done") { withAnimation { editMode = .inactive } }
            } else {
                if model.hasActionableRows {
                    Button("Select") { withAnimation { editMode = .active } }
                        .disabled(model.isBusy)
                }
                if model.capabilities.commit {
                    Button { model.openCommit() } label: {
                        Label("Commit…", systemImage: "checkmark.circle")
                    }
                    .disabled(model.stagedCount == 0 || model.isBusy)
                    .accessibilityLabel("Commit")
                }
                if model.capabilities.hasSync {
                    Button { model.openSync() } label: {
                        Label("Sync…", systemImage: "arrow.triangle.2.circlepath")
                    }
                    .accessibilityLabel("Sync")
                }
            }
        }
        // The selection toolbar: each action acts on the ticked rows it
        // applies to and is enabled only while that subset is non-empty.
        if editMode.isEditing {
            ToolbarItemGroup(placement: .bottomBar) {
                let subsets = model.selectionSubsets
                if model.capabilities.stage {
                    Button("Stage") {
                        Task {
                            await model.stage(subsets.stage)
                            endSelectionIfEmpty()
                        }
                    }
                    .disabled(subsets.stage.isEmpty || model.isBusy)
                    Spacer()
                    Button("Unstage") {
                        Task {
                            await model.unstage(subsets.unstage)
                            endSelectionIfEmpty()
                        }
                    }
                    .disabled(subsets.unstage.isEmpty || model.isBusy)
                }
                if model.capabilities.discard {
                    Spacer()
                    Button("Discard…", role: .destructive) { model.requestDiscard(subsets.discard) }
                        .disabled(subsets.discard.isEmpty || model.isBusy)
                }
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            LoadingIndicator()
                .pixelScreenBackground()
        case .ready(let status, let repo, let sections):
            changesList(status: status, repo: repo, sections: sections)
        case .unavailable(let reason, let cwd):
            let copy = SessionGitCopy.unavailable(reason)
            SessionGitStateView(copy.title, systemImage: "folder.badge.questionmark", description: copy.body, detail: cwd)
        case .agentOutdated:
            SessionGitStateView(
                "Update your agent",
                systemImage: "arrow.down.circle",
                description: "This agent does not serve the git Changes API yet. Update it to view changes here."
            )
        case .sessionGone:
            SessionGitStateView(
                "Session ended",
                systemImage: "moon.zzz",
                description: "This session is no longer running on the agent."
            )
        case .error(let message):
            SessionGitStateView("Couldn't load changes", systemImage: "exclamationmark.triangle", description: message) {
                Button("Retry") { Task { await model.load() } }
                    .buttonStyle(.pixelSmall)
            }
        }
    }

    private func changesList(status: GitStatusResponse, repo: GitRepoInfo, sections: [GitChangesSection]) -> some View {
        List(selection: $model.selection) {
            Section {
                headerCard(repo: repo, cwd: status.cwd)
                    .pixelListRow()
                    .selectionDisabled()
                if let banner = GitChangesDisplay.rebaseBanner(repo, conflicts: model.conflictCount) {
                    rebaseBanner(banner)
                        .pixelListRow()
                        .selectionDisabled()
                }
                if let staleError = model.staleError {
                    staleBanner(staleError)
                        .pixelListRow()
                        .selectionDisabled()
                }
                if let banner = model.job {
                    jobBanner(banner)
                        .pixelListRow()
                        .selectionDisabled()
                }
                if status.truncated {
                    Text(GitChangesDisplay.truncatedNotice(status))
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .pixelInfoBanner()
                        .pixelListRow()
                        .selectionDisabled()
                }
                if sections.isEmpty {
                    PixelEmptyState("Working tree clean", systemImage: "checkmark.circle")
                        .pixelListRow()
                        .selectionDisabled()
                }
            }
            ForEach(sections) { section in
                Section(header: sectionHeader(section)) {
                    ForEach(section.rows) { row in
                        self.row(row)
                            .pixelCard(selected: editMode.isEditing && model.selection.contains(row.id))
                            .pixelListRow()
                    }
                }
            }
        }
        .pixelList()
    }

    /// A tap opens the diff, a leading swipe stages or unstages (a full swipe
    /// counts), a trailing swipe offers Discard behind its confirmation, and
    /// the context menu repeats all three plus Select. In edit mode the row is
    /// plain content so the `List` owns the tap.
    @ViewBuilder
    private func row(_ row: GitChangesRow) -> some View {
        let actions = GitChangesDisplay.rowActions(row.entry, capabilities: model.capabilities)
        if editMode.isEditing {
            if actions.any {
                GitChangesRowView(entry: row.entry, chevron: false)
            } else {
                GitChangesRowView(entry: row.entry, chevron: false)
                    .selectionDisabled()
            }
        } else {
            Button {
                router.navigate(to: model.diffRoute(for: row.entry))
            } label: {
                GitChangesRowView(entry: row.entry, chevron: true)
            }
            .buttonStyle(.plain)
            .swipeActions(edge: .leading, allowsFullSwipe: true) {
                if actions.resolve {
                    Button { Task { await model.resolve(row) } } label: {
                        Label("Mark resolved", systemImage: "checkmark")
                    }
                    .tint(colors.accent)
                }
                if actions.stage {
                    Button { Task { await model.stage([row]) } } label: {
                        Label("Stage", systemImage: "plus")
                    }
                    .tint(colors.accent)
                }
                if actions.unstage {
                    Button { Task { await model.unstage([row]) } } label: {
                        Label("Unstage", systemImage: "minus")
                    }
                    .tint(colors.accent)
                }
            }
            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                if actions.discard {
                    Button(role: .destructive) { model.requestDiscard([row]) } label: {
                        Label("Discard…", systemImage: "trash")
                    }
                }
            }
            .contextMenu {
                if actions.resolve {
                    Button { Task { await model.resolve(row) } } label: {
                        Label("Mark resolved", systemImage: "checkmark")
                    }
                }
                if actions.stage {
                    Button { Task { await model.stage([row]) } } label: {
                        Label("Stage", systemImage: "plus")
                    }
                }
                if actions.unstage {
                    Button { Task { await model.unstage([row]) } } label: {
                        Label("Unstage", systemImage: "minus")
                    }
                }
                if actions.discard {
                    Button(role: .destructive) { model.requestDiscard([row]) } label: {
                        Label("Discard…", systemImage: "trash")
                    }
                }
                if actions.any {
                    Button {
                        model.selection.insert(row.id)
                        withAnimation { editMode = .active }
                    } label: {
                        Label("Select", systemImage: "checkmark.circle")
                    }
                }
            }
            .disabled(model.isBusy && (actions.any || actions.resolve))
        }
    }

    private func sectionHeader(_ section: GitChangesSection) -> some View {
        HStack {
            PixelSectionHeader(section.title)
            Spacer()
            Text("\(section.rows.count)")
                .chromeText()
                .foregroundStyle(colors.textMuted)
            if !editMode.isEditing,
               let action = GitChangesDisplay.sectionAction(section, capabilities: model.capabilities) {
                Button(action.label) {
                    Task {
                        if action.stage { await model.stage(section.rows) } else { await model.unstage(section.rows) }
                    }
                }
                .buttonStyle(.pixelSmall)
                .disabled(model.isBusy)
                .accessibilityLabel("\(action.label) \(section.title)")
            }
        }
    }

    private func headerCard(repo: GitRepoInfo, cwd: String?) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: "arrow.triangle.branch")
                    .foregroundStyle(colors.accent)
                Text(GitChangesDisplay.headLabel(repo))
                    .font(.body.weight(.semibold))
                    .foregroundStyle(colors.text)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 8)
                if let state = GitChangesDisplay.stateLabel(repo.state) {
                    PixelChip(state, .status(.warning))
                }
                if repo.is_linked_worktree {
                    PixelChip("worktree", .status(.muted))
                }
            }
            // The ahead/behind counts are what a fetch, pull or push acts on,
            // so the line itself opens the sheet once the agent can run them.
            if let upstream = GitChangesDisplay.upstreamLabel(repo.upstream) {
                if model.capabilities.hasSync {
                    Button { model.openSync() } label: {
                        Text(upstream)
                            .font(.caption)
                            .monospaced()
                            .foregroundStyle(colors.accent)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("git-changes-upstream")
                } else {
                    Text(upstream)
                        .font(.caption)
                        .monospaced()
                        .foregroundStyle(colors.textMuted)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
            if let cwd {
                Text(cwd)
                    .font(.caption)
                    .monospaced()
                    .foregroundStyle(colors.textMuted)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .pixelCard()
    }

    /// A rebase in progress, for as long as the status says so: where it
    /// stopped and what to do next, with Continue (once no conflict is left)
    /// and the two confirmed ways out.
    private func rebaseBanner(_ banner: GitChangesDisplay.RebaseBanner) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 4) {
                Text(banner.title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(colors.warning)
                if let progress = banner.progress {
                    Text(progress)
                        .font(.caption)
                        .monospaced()
                        .foregroundStyle(colors.textMuted)
                        .lineLimit(2)
                        .truncationMode(.tail)
                }
                Text(banner.hint)
                    .font(.footnote)
                    .foregroundStyle(colors.text)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .pixelInfoBanner()
            if model.capabilities.rebase {
                HStack(spacing: 12) {
                    Button("Continue") { Task { await model.continueRebase() } }
                        .buttonStyle(.pixelSmall)
                        .disabled(model.isBusy || model.conflictCount > 0)
                        .accessibilityIdentifier("git-changes-rebase-continue")
                    Button("Skip…") { model.requestRebaseStep(.skip) }
                        .buttonStyle(.pixelSmall)
                        .disabled(model.isBusy)
                        .accessibilityIdentifier("git-changes-rebase-skip")
                    Button("Abort…") { model.requestRebaseStep(.abort) }
                        .buttonStyle(.pixelSmallDanger)
                        .disabled(model.isBusy)
                        .accessibilityIdentifier("git-changes-rebase-abort")
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("git-changes-rebase")
    }

    private func staleBanner(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            PixelErrorBanner(message)
            Button("Retry") { Task { await model.load() } }
                .buttonStyle(.pixelSmall)
        }
    }

    /// A running fetch / pull / push, or its result until dismissed: the title
    /// and git's latest line with the bar while it runs, Cancel beside it; the
    /// outcome afterwards, with Options… leading back to the sheet's follow-ups
    /// when it failed.
    private func jobBanner(_ banner: GitJobBanner) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if banner.failed {
                PixelErrorBanner(banner.text)
            } else {
                Text(banner.text)
                    .font(.footnote)
                    .foregroundStyle(banner.running ? colors.text : colors.ok)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .pixelInfoBanner()
            }
            if banner.running, let job = banner.job {
                PixelProgressBar(percent: job.percent)
                if let line = job.line, !line.isEmpty {
                    Text(line)
                        .font(.caption)
                        .monospaced()
                        .foregroundStyle(colors.textMuted)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            HStack(spacing: 12) {
                if banner.running {
                    Button("Cancel") { Task { await model.cancelSync() } }
                        .buttonStyle(.pixelSmall)
                        .accessibilityIdentifier("git-changes-job-cancel")
                } else {
                    if banner.failed, model.capabilities.hasSync {
                        Button("Options…") { model.openSync() }
                            .buttonStyle(.pixelSmall)
                            .accessibilityIdentifier("git-changes-job-options")
                    }
                    Button("Dismiss") { model.dismissJobResult() }
                        .buttonStyle(.pixelSmall)
                        .accessibilityIdentifier("git-changes-job-dismiss")
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("git-changes-job")
    }
}

/// One Changes row (Android `GitChangesRow`): status badge, file name, the
/// rename source or directory, and a submodule marker.
private struct GitChangesRowView: View {
    let entry: GitStatusEntry
    var chevron = true

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        let badge = GitChangesDisplay.badge(for: entry)
        HStack(alignment: .top, spacing: 12) {
            PixelChip(badge.label, .status(badge.tone))
            VStack(alignment: .leading, spacing: 4) {
                Text(entry.fileName)
                    .font(.body)
                    .foregroundStyle(colors.text)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if let secondary = GitChangesDisplay.secondaryLabel(entry) {
                    Text(secondary)
                        .font(.caption)
                        .monospaced()
                        .foregroundStyle(colors.textMuted)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                if entry.submodule {
                    PixelChip("submodule", .status(.muted))
                }
            }
            Spacer(minLength: 0)
            if chevron {
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundStyle(colors.gray)
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
