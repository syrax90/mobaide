import SwiftUI

/// Session-scoped working-tree overview (Android `GitChangesScreen`), pushed
/// from the terminal's `⋯ → Git → Changes…`.
struct GitChangesScreen: View {
    let sessionId: String
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            GitChangesContent(container: container, sessionId: sessionId)
        }
    }
}

private struct GitChangesContent: View {
    @State private var model: GitChangesModel

    @Environment(Router.self) private var router
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.mobaideColors) private var colors

    init(container: AppContainer, sessionId: String) {
        let sessionRepository = container.sessionRepository
        _model = State(initialValue: GitChangesModel(
            sessionId: sessionId,
            agentAPI: container.agentAPI,
            agentEventBus: container.agentEventBus,
            projectName: {
                sessionRepository.currentSessions.first { $0.session_id == sessionId }?.project_name
            }
        ))
    }

    var body: some View {
        content
            .navigationTitle("Changes")
            .navigationBarTitleDisplayMode(.inline)
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
            .holdsSessionWatch(model.sessionId)
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
        List {
            Section {
                headerCard(repo: repo, cwd: status.cwd)
                    .pixelListRow()
                if let staleError = model.staleError {
                    staleBanner(staleError)
                        .pixelListRow()
                }
                if status.truncated {
                    Text(GitChangesDisplay.truncatedNotice(status))
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .pixelInfoBanner()
                        .pixelListRow()
                }
                if sections.isEmpty {
                    PixelEmptyState("Working tree clean", systemImage: "checkmark.circle")
                        .pixelListRow()
                }
            }
            ForEach(sections) { section in
                Section(header: sectionHeader(section)) {
                    ForEach(section.rows) { row in
                        Button {
                            router.navigate(to: model.diffRoute(for: row.entry))
                        } label: {
                            GitChangesRowView(entry: row.entry)
                        }
                        .buttonStyle(.plain)
                        .pixelCard()
                        .pixelListRow()
                    }
                }
            }
        }
        .pixelList()
    }

    private func sectionHeader(_ section: GitChangesSection) -> some View {
        HStack {
            PixelSectionHeader(section.title)
            Spacer()
            Text("\(section.rows.count)")
                .chromeText()
                .foregroundStyle(colors.textMuted)
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
            if let upstream = GitChangesDisplay.upstreamLabel(repo.upstream) {
                Text(upstream)
                    .font(.caption)
                    .monospaced()
                    .foregroundStyle(colors.textMuted)
                    .lineLimit(1)
                    .truncationMode(.middle)
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

    private func staleBanner(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            PixelErrorBanner(message)
            Button("Retry") { Task { await model.load() } }
                .buttonStyle(.pixelSmall)
        }
    }
}

/// One Changes row (Android `GitChangesRow`): status badge, file name, the
/// rename source or directory, and a submodule marker.
private struct GitChangesRowView: View {
    let entry: GitStatusEntry

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
            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(colors.gray)
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
