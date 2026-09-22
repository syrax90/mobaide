import SwiftUI

/// What the Changes screen presents the resolver for.
struct GitConflictTarget: Identifiable, Equatable {
    let sessionId: String
    let path: String

    var id: String { path }
}

/// The conflict resolver (Android `GitConflictScreen`): the conflicted file,
/// markers and all, in an editor, with the two sides named above it and the
/// whole-file picks behind a menu.
///
/// A full-screen cover with its own `NavigationStack`, not a push: a pushed
/// view cannot intercept the swipe-back gesture, and an editor holding the
/// only copy of the user's work must be able to ask before it goes.
struct GitConflictScreen: View {
    let target: GitConflictTarget
    /// The cover is done; `message` is what the Changes list should say.
    let onClosed: (_ message: String?) -> Void
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            NavigationStack {
                GitConflictContent(container: container, target: target, onClosed: onClosed)
            }
        }
    }
}

private struct GitConflictContent: View {
    @State private var model: GitConflictModel
    @State private var legendOpen = false
    @State private var showDiff = false
    @State private var pendingTake: TakeChoice?
    @State private var startOverPresented = false
    @State private var discardPresented = false
    private let onClosed: (String?) -> Void

    @Environment(\.mobaideColors) private var colors

    init(container: AppContainer, target: GitConflictTarget, onClosed: @escaping (String?) -> Void) {
        _model = State(initialValue: GitConflictModel(
            sessionId: target.sessionId,
            path: target.path,
            agentAPI: container.agentAPI
        ))
        self.onClosed = onClosed
    }

    var body: some View {
        content
            .navigationTitle(model.fileName)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbarContent }
            .task { await model.load() }
            .onChange(of: model.outcome) { _, outcome in
                if let outcome { onClosed(outcome.message) }
            }
            .navigationDestination(isPresented: $showDiff) {
                GitDiffScreen(
                    sessionId: model.sessionId,
                    path: model.path,
                    scope: GitDiffResponse.scopeUnstaged,
                    origPath: nil
                )
            }
            // `presenting:` hands the choice to the action closure: SwiftUI
            // clears `isPresented` before that closure runs.
            .alert(
                pendingTake?.title ?? "",
                isPresented: takePresented,
                presenting: pendingTake
            ) { choice in
                Button(choice.confirm, role: choice.danger ? .destructive : nil) {
                    Task { await model.take(choice) }
                }
                Button("Cancel", role: .cancel) {}
            } message: { choice in
                Text(choice.body)
            }
            .alert(GitConflictDisplay.startOverPrompt(model.path).title, isPresented: $startOverPresented) {
                Button(GitConflictDisplay.startOverLabel, role: .destructive) {
                    Task { await model.startOver(withOriginalLines: false) }
                }
                Button(GitConflictDisplay.startOverWithBaseLabel, role: .destructive) {
                    Task { await model.startOver(withOriginalLines: true) }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(GitConflictDisplay.startOverPrompt(model.path).body)
            }
            .alert(GitConflictDisplay.discardEditsPrompt(model.path).title, isPresented: $discardPresented) {
                Button("Discard", role: .destructive) { onClosed(nil) }
                Button("Keep editing", role: .cancel) {}
            } message: {
                Text(GitConflictDisplay.discardEditsPrompt(model.path).body)
            }
            .toast($model.toast)
            .interactiveDismissDisabled(model.isDirty)
            .holdsSessionWatch(model.sessionId)
    }

    private var takePresented: Binding<Bool> {
        Binding(get: { pendingTake != nil }, set: { if !$0 { pendingTake = nil } })
    }

    private func close() {
        if model.isDirty {
            discardPresented = true
        } else {
            onClosed(nil)
        }
    }

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .cancellationAction) {
            Button("Close", action: close)
                .accessibilityIdentifier("git-conflict-close")
        }
        if let conflict = model.conflict {
            ToolbarItem(placement: .primaryAction) {
                Menu {
                    // A conflict that cannot be edited shows its picks in the body instead.
                    if GitConflictDisplay.isEditable(conflict) {
                        ForEach(GitConflictDisplay.takeChoices(conflict)) { choice in
                            Button("\(choice.label)…", role: choice.danger ? .destructive : nil) {
                                pendingTake = choice
                            }
                        }
                    }
                    if GitConflictDisplay.canStartOver(conflict) {
                        Button("\(GitConflictDisplay.startOverLabel)…") { startOverPresented = true }
                    }
                    Button("View diff") { showDiff = true }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .menuOrder(.fixed)
                .disabled(model.isBusy)
                .accessibilityLabel("More conflict actions")
                .accessibilityIdentifier("git-conflict-menu")
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            LoadingIndicator()
                .pixelScreenBackground()
        case .ready(let conflict):
            VStack(spacing: 0) {
                header(conflict)
                if model.isGone { goneStrip }
                if model.isStale { staleStrip }
                if GitConflictDisplay.isEditable(conflict) {
                    editor(conflict)
                    actionBar
                } else {
                    blocked(conflict)
                }
            }
            .pixelScreenBackground()
        case .agentOutdated:
            SessionGitStateView(
                "Update your agent",
                systemImage: "arrow.down.circle",
                description: "This agent cannot resolve conflicts yet. Update it, or resolve them in the terminal."
            )
        case .sessionGone:
            SessionGitStateView(
                "Session ended",
                systemImage: "moon.zzz",
                description: "This session is no longer running on the agent."
            )
        case .error(let message):
            SessionGitStateView(
                "Couldn't load the file", systemImage: "exclamationmark.triangle", description: message
            ) {
                Button("Retry") { Task { await model.load() } }
                    .buttonStyle(.pixelSmall)
            }
        }
    }

    private func header(_ conflict: GitConflictResponse) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(conflict.path)
                .font(MobaideTypography.code)
                .foregroundStyle(colors.textMuted)
                .lineLimit(2)
            Text(GitConflictDisplay.caption(conflict))
                .font(.footnote)
                .foregroundStyle(colors.text)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    private var goneStrip: some View {
        strip(GitConflictDisplay.resolvedElsewhereNotice(model.path)) {
            Button("Close", action: close)
                .buttonStyle(.pixelSmall)
        }
    }

    private var staleStrip: some View {
        strip(GitConflictDisplay.fileChangedNotice(model.path)) {
            Button("Reload file") { Task { await model.reloadFile() } }
                .buttonStyle(.pixelSmall)
                .accessibilityIdentifier("git-conflict-reload")
            Button("Keep my edits") { Task { await model.keepMyEdits() } }
                .buttonStyle(.pixelSmall)
                .accessibilityIdentifier("git-conflict-keep-edits")
        }
        .disabled(model.isBusy)
    }

    private func strip<Actions: View>(_ text: String, @ViewBuilder actions: () -> Actions) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(text)
                .font(.footnote)
                .foregroundStyle(colors.danger)
            HStack(spacing: 8) { actions() }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(colors.surface)
    }

    private func editor(_ conflict: GitConflictResponse) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Text(GitConflictDisplay.conflictsLeftLabel(model.scan))
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(model.scan.hasMarkers ? colors.warning : colors.ok)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityIdentifier("git-conflict-count")
                Button("Next") { model.jumpToNextConflict() }
                    .buttonStyle(.pixelSmall)
                    .disabled(model.scan.conflicts == 0)
                    .accessibilityIdentifier("git-conflict-next")
                Button(legendOpen ? "Hide sides" : "Which side?") { legendOpen.toggle() }
                    .buttonStyle(.pixelSmall)
                    .accessibilityIdentifier("git-conflict-legend-toggle")
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 6)
            if legendOpen {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(GitConflictDisplay.legend(conflict, hasBase: model.scan.hasBase), id: \.self) { line in
                        Text(line)
                            .font(.footnote)
                            .foregroundStyle(colors.textMuted)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16)
                .padding(.bottom, 6)
                .accessibilityIdentifier("git-conflict-legend")
            }
            if model.overwriteWarning {
                Text(GitConflictDisplay.keepEditsWarning)
                    .font(.footnote)
                    .foregroundStyle(colors.danger)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 4)
            }
            if model.scan.tooLarge, let limit = model.conflict?.byte_limit {
                Text(GitConflictDisplay.tooLargeToSaveNotice(limit))
                    .font(.footnote)
                    .foregroundStyle(colors.danger)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 4)
                    .accessibilityIdentifier("git-conflict-too-large")
            }
            PixelDivider()
            CodeTextView(
                buffer: model.buffer,
                isEditable: !model.isBusy && !model.isGone,
                textColor: UIColor(colors.text),
                accessibilityIdentifier: "git-conflict-editor"
            )
            .background(colors.bg)
        }
    }

    private var actionBar: some View {
        VStack(spacing: 0) {
            PixelDivider()
            HStack(spacing: 8) {
                Spacer()
                Button("Save draft") { Task { await model.saveDraft() } }
                    .buttonStyle(.pixel)
                    .disabled(!model.canSaveDraft)
                    .accessibilityIdentifier("git-conflict-draft")
                Button(model.isBusy ? "Saving…" : "Apply & mark resolved") { Task { await model.apply() } }
                    .buttonStyle(.pixelPrimary)
                    .disabled(!model.canApply)
                    .accessibilityIdentifier("git-conflict-apply")
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
    }

    private func blocked(_ conflict: GitConflictResponse) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text(GitConflictDisplay.kindNotice(conflict) ?? "")
                    .font(.body)
                    .foregroundStyle(colors.text)
                ForEach(GitConflictDisplay.takeChoices(conflict)) { choice in
                    if choice.danger {
                        Button("\(choice.label)…") { pendingTake = choice }
                            .buttonStyle(.pixelDangerBlock)
                    } else {
                        Button("\(choice.label)…") { pendingTake = choice }
                            .buttonStyle(.pixelBlock)
                    }
                }
                .disabled(model.isBusy || model.isGone)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
        }
    }
}
