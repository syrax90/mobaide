import SwiftUI

/// Per-file diff (Android `GitDiffScreen`): the agent's hunks as one lazy row
/// per line in a scroller on both axes. Every line's text is pinned to the
/// widest line, so the rows move sideways together and a long line is scrolled
/// to rather than cut; the line numbers stay at the visible leading edge.
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
    @Environment(\.dismiss) private var dismiss

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
                    if let action = model.stageAction {
                        Button(action.label) { Task { await model.runStageAction() } }
                            .disabled(model.isMutating || !isReady)
                            .accessibilityIdentifier("git-diff-stage-action")
                    }
                }
            }
            .refreshable { await model.load() }
            .task(id: model.loadKey) { await model.load() }
            .task { await model.loadCapabilities() }
            .toast($model.toast)
            // The file moved to another section; the list is where to see it.
            .onChange(of: model.didMove) { _, moved in
                if moved { dismiss() }
            }
            .holdsSessionWatch(model.sessionId)
    }

    private var isReady: Bool {
        if case .ready = model.state { return true }
        return false
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            LoadingIndicator()
                .pixelScreenBackground()
        case .ready(let diff, let rows, let metrics):
            VStack(spacing: 0) {
                header(diff)
                    .padding(PixelMetrics.listRowInsets)
                diffBody(rows: rows, metrics: metrics)
            }
            .pixelScreenBackground()
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

    private func diffBody(rows: [DiffRow], metrics: DiffMetrics) -> some View {
        GeometryReader { geometry in
            let viewportWidth = geometry.size.width
            ScrollView([.horizontal, .vertical]) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(rows) { row in
                        DiffRowView(row: row, metrics: metrics, viewportWidth: viewportWidth)
                    }
                }
                .frame(width: max(viewportWidth, DiffRowView.lineWidth(metrics)), alignment: .leading)
                .padding(.bottom, 24)
                // A two-axis scroller centres content smaller than itself.
                .frame(minHeight: geometry.size.height, alignment: .top)
            }
            .background(colors.bg2)
        }
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
                PixelSegmented(
                    GitDiffModel.contextChoices,
                    selection: $model.context,
                    accessibilityLabel: "Diff view"
                )
                .frame(maxWidth: 240)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .pixelCard()
    }
}

private struct DiffRowView: View {
    private static let gutterLeading: CGFloat = 4
    private static let markerPadding: CGFloat = 6
    private static let textTrailing: CGFloat = 12

    /// The width of a line row: both gutters, the marker and the pinned text.
    static func lineWidth(_ metrics: DiffMetrics) -> CGFloat {
        2 * (gutterLeading + metrics.gutterWidth)
            + metrics.cell + 2 * markerPadding
            + metrics.textWidth + textTrailing
    }

    let row: DiffRow
    let metrics: DiffMetrics
    let viewportWidth: CGFloat

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        rowContent
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(background)
    }

    @ViewBuilder
    private var rowContent: some View {
        switch row.kind {
        case .hunkHeader(let range, let heading):
            pinnedToViewport(
                Text(GitDiffDisplay.hunkHeaderText(range: range, heading: heading))
                    .font(MobaideTypography.code)
                    .foregroundStyle(colors.accent)
                    .padding(.vertical, 6)
                    .padding(.horizontal, 12)
            )
        case .line(let type, let old, let new, let text):
            let tone = GitDiffDisplay.lineTone(type).map(colors.color)
            HStack(alignment: .top, spacing: 0) {
                // Drawn above the text, on the row's own opaque fill, so the
                // text scrolls underneath the numbers.
                HStack(alignment: .top, spacing: 0) {
                    gutter(old)
                    gutter(new)
                    Text(GitDiffDisplay.marker(type))
                        .font(MobaideTypography.code)
                        .foregroundStyle(tone ?? colors.textMuted)
                        .frame(width: metrics.cell)
                        .padding(.horizontal, Self.markerPadding)
                }
                .background(background)
                .stickyLeading()
                .zIndex(1)
                Text(text)
                    .font(MobaideTypography.code)
                    .foregroundStyle(tone ?? colors.text)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                    .frame(width: metrics.textWidth, alignment: .leading)
                    .padding(.trailing, Self.textTrailing)
            }
            .padding(.vertical, 1)
        case .noNewline:
            pinnedToViewport(
                Text("\\ No newline at end of file")
                    .font(MobaideTypography.code)
                    .foregroundStyle(colors.textMuted)
                    .padding(.leading, 12)
                    .padding(.vertical, 1)
            )
        case .truncated(let shown, let total):
            pinnedToViewport(
                Text(GitDiffDisplay.truncatedNotice(shown: shown, total: total))
                    .font(.footnote)
                    .foregroundStyle(colors.warning)
                    .padding(8)
            )
        case .notice(let text):
            pinnedToViewport(
                Text(text)
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
                    .padding(8)
            )
        }
    }

    private var background: some View {
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

    /// A non-code row wraps at the screen width and never scrolls out of view.
    private func pinnedToViewport(_ content: some View) -> some View {
        content
            .frame(width: viewportWidth, alignment: .leading)
            .stickyLeading()
    }

    private func gutter(_ number: Int?) -> some View {
        Text(number.map(String.init) ?? "")
            .font(MobaideTypography.code)
            .monospacedDigit()
            .foregroundStyle(colors.textMuted)
            .lineLimit(1)
            .fixedSize(horizontal: true, vertical: false)
            .frame(width: metrics.gutterWidth, alignment: .trailing)
            .padding(.leading, Self.gutterLeading)
    }
}

private extension View {
    /// Holds the view at the scroller's visible leading edge while the diff
    /// scrolls sideways. A render-time effect: no state changes per frame.
    func stickyLeading() -> some View {
        visualEffect { content, proxy in
            content.offset(x: max(0, -proxy.frame(in: .scrollView).minX))
        }
    }
}
