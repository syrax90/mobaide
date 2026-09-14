import SwiftUI

/// Per-file diff (Android `GitDiffScreen`): the agent's hunks as one `List`
/// row per line. Long lines are cut at one line rather than scrolled sideways
/// — a second scroll axis fights the list, and a per-row scroller de-lazifies
/// it — with a "Wrap lines" menu action for reading them whole.
struct GitDiffScreen: View {
    let sessionId: String
    let path: String
    let scope: String
    let origPath: String?
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            GitDiffContent(container: container, sessionId: sessionId, path: path, scope: scope, origPath: origPath)
        }
    }
}

private struct GitDiffContent: View {
    @State private var model: GitDiffModel

    @Environment(\.mobaideColors) private var colors

    init(container: AppContainer, sessionId: String, path: String, scope: String, origPath: String?) {
        _model = State(initialValue: GitDiffModel(
            sessionId: sessionId,
            path: path,
            scope: scope,
            origPath: origPath,
            agentAPI: container.agentAPI
        ))
    }

    var body: some View {
        content
            .navigationTitle(model.fileName)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        // A `Button`, not a `Toggle`: the theme installs the
                        // pixel checkbox toggle style app-wide.
                        Button {
                            model.wrapLines.toggle()
                        } label: {
                            Label("Wrap lines", systemImage: model.wrapLines ? "checkmark.square" : "square")
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .menuOrder(.fixed)
                    .accessibilityLabel("More options")
                }
            }
            .refreshable { await model.load() }
            .task(id: model.loadKey) { await model.load() }
            .holdsSessionWatch(model.sessionId)
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            LoadingIndicator()
                .pixelScreenBackground()
        case .ready(let diff, let rows, let gutterDigits):
            diffList(diff: diff, rows: rows, gutterDigits: gutterDigits)
        case .agentOutdated:
            SessionGitStateView(
                "Update your agent",
                systemImage: "arrow.down.circle",
                description: "This agent does not serve the git Changes API yet. Update it to view diffs here."
            )
        case .sessionGone:
            SessionGitStateView(
                "Session ended",
                systemImage: "moon.zzz",
                description: "This session is no longer running on the agent."
            )
        case .error(let message):
            SessionGitStateView("Couldn't load diff", systemImage: "exclamationmark.triangle", description: message) {
                Button("Retry") { model.retry() }
                    .buttonStyle(.pixelSmall)
            }
        }
    }

    private func diffList(diff: GitDiffResponse, rows: [DiffRow], gutterDigits: Int) -> some View {
        List {
            Section {
                header(diff)
                    .pixelListRow()
            }
            Section {
                ForEach(rows) { row in
                    DiffRowView(row: row, gutterDigits: gutterDigits, wrap: model.wrapLines)
                        .listRowBackground(rowBackground(row))
                        .listRowSeparator(.hidden)
                        .listRowInsets(EdgeInsets(top: 0, leading: 8, bottom: 0, trailing: 8))
                }
            }
        }
        .pixelList()
        // Rows are as tall as one code line, not the 44pt list default.
        .environment(\.defaultMinListRowHeight, 0)
    }

    private func header(_ diff: GitDiffResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                if let change = diff.change {
                    PixelChip(GitDiffDisplay.changeLabel(change), .status(GitDiffDisplay.changeTone(change)))
                }
                if GitDiffDisplay.showsCounts(diff) {
                    Text("+\(diff.additions)")
                        .font(MobaideTypography.code)
                        .foregroundStyle(colors.ok)
                    Text("−\(diff.deletions)")
                        .font(MobaideTypography.code)
                        .foregroundStyle(colors.danger)
                }
                if let mode = GitDiffDisplay.modeChange(diff) {
                    Text(mode)
                        .font(.caption)
                        .monospaced()
                        .foregroundStyle(colors.textMuted)
                }
                Spacer(minLength: 0)
                if model.isLoading {
                    PixelLoadingBlock(width: 6, height: 11)
                }
            }
            if let orig = diff.orig_path {
                Text("from \(orig)")
                    .font(.caption)
                    .monospaced()
                    .foregroundStyle(colors.textMuted)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            if GitDiffDisplay.showsContextChoices(diff) {
                HStack(spacing: 12) {
                    Text("Context")
                        .sectionHeading()
                    PixelSegmented(
                        GitDiffModel.contextChoices.map { ($0, String($0)) },
                        selection: $model.context,
                        accessibilityLabel: "Context lines"
                    )
                    .frame(maxWidth: 180)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .pixelCard()
    }

    private func rowBackground(_ row: DiffRow) -> some View {
        let tint: Color? = {
            if case .line(let type, _, _, _) = row.kind, let tone = GitDiffDisplay.lineTone(type) {
                return colors.color(tone).opacity(0.12)
            }
            if case .hunkHeader = row.kind {
                return colors.surface2
            }
            return nil
        }()
        return colors.bg2.overlay(tint ?? Color.clear)
    }
}

private struct DiffRowView: View {
    let row: DiffRow
    let gutterDigits: Int
    let wrap: Bool

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        switch row.kind {
        case .hunkHeader(let range, let heading):
            Text(GitDiffDisplay.hunkHeaderText(range: range, heading: heading))
                .font(MobaideTypography.code)
                .foregroundStyle(colors.accent)
                .lineLimit(wrap ? nil : 1)
                .truncationMode(.tail)
                .padding(.vertical, 6)
                .padding(.horizontal, 4)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .line(let type, let old, let new, let text):
            let tone = GitDiffDisplay.lineTone(type).map(colors.color)
            HStack(alignment: .top, spacing: 0) {
                gutter(old)
                gutter(new)
                Text(GitDiffDisplay.marker(type))
                    .font(MobaideTypography.code)
                    .foregroundStyle(tone ?? colors.textMuted)
                    .padding(.horizontal, 6)
                Text(text)
                    .font(MobaideTypography.code)
                    .foregroundStyle(tone ?? colors.text)
                    .lineLimit(wrap ? nil : 1)
                    .truncationMode(.tail)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.vertical, 1)
        case .noNewline:
            Text("\\ No newline at end of file")
                .font(MobaideTypography.code)
                .foregroundStyle(colors.textMuted)
                .padding(.leading, 12)
                .padding(.vertical, 1)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .truncated(let shown, let total):
            Text(GitDiffDisplay.truncatedNotice(shown: shown, total: total))
                .font(.footnote)
                .foregroundStyle(colors.warning)
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .notice(let text):
            Text(text)
                .font(.footnote)
                .foregroundStyle(colors.textMuted)
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// Sized by a hidden run of the widest digit so the column never shifts
    /// between rows.
    private func gutter(_ number: Int?) -> some View {
        Text(String(repeating: "8", count: gutterDigits))
            .font(MobaideTypography.code)
            .hidden()
            .overlay(alignment: .trailing) {
                Text(number.map(String.init) ?? "")
                    .font(MobaideTypography.code)
                    .monospacedDigit()
                    .foregroundStyle(colors.textMuted)
            }
            .padding(.leading, 4)
    }
}
