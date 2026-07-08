import SwiftUI

/// Live autoscan monitor (Android `AutoScanDetailScreen`), the real body for the
/// `autoScanDetail` route. Streams phases + an ANSI-colored log off the dedicated
/// `ws/autoscan` channel; the stream is torn down on disappear.
struct AutoScanDetailScreen: View {
    let projectName: String
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            AutoScanDetailContent(container: container, projectName: projectName)
        }
    }
}

private struct AutoScanDetailContent: View {
    @State private var model: AutoScanDetailModel
    @State private var toast: ToastMessage?

    init(container: AppContainer, projectName: String) {
        _model = State(initialValue: AutoScanDetailModel(projectName: projectName, repository: container.autoScanRepository))
    }

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 16) {
                headerCard
                phaseTimeline
            }
            .padding()
            logPane
        }
        .navigationTitle(model.projectName)
        .navigationBarTitleDisplayMode(.inline)
        .toast($toast)
        .task { await model.run() }
        .task {
            for await error in model.errors() {
                toast = ToastMessage(text: error.message)
            }
        }
    }

    private var headerCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Circle()
                    .fill(AutoScanDisplay.statusColor(model.statusText))
                    .frame(width: 10, height: 10)
                Text(model.statusText.capitalized)
                    .font(.headline)
                if model.cycleNumber > 0 {
                    Text("· cycle #\(model.cycleNumber)")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            CountdownView(nextScanAt: model.nextScanAt)
            HStack {
                Button("Run Now") { Task { await model.runNow() } }
                    .buttonStyle(.borderedProminent)
                Button("Stop") { Task { await model.stopScan() } }
                    .buttonStyle(.bordered)
                    .tint(.red)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(.secondarySystemBackground)))
    }

    private var phaseTimeline: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(model.orderedPhases, id: \.name) { phase in
                HStack(spacing: 8) {
                    Image(systemName: phaseIcon(phase.state.status))
                        .foregroundStyle(AutoScanDisplay.phaseColor(phase.state.status))
                    Text(phase.name.capitalized)
                        .font(phase.name == model.activePhase ? .body.bold() : .body)
                    Text(phase.state.status)
                        .font(.caption)
                        .foregroundStyle(AutoScanDisplay.phaseColor(phase.state.status))
                    Spacer()
                }
            }
            if !model.recentIssues.isEmpty {
                Text("Recent issues:")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.top, 4)
                ForEach(Array(model.recentIssues.suffix(5).enumerated()), id: \.offset) { _, issue in
                    Text("\(issue.identifier): \(issue.title) (\(issue.step)/\(issue.status))")
                        .font(.caption)
                        .foregroundStyle(AutoScanDisplay.issueColor(issue.status))
                        .lineLimit(1)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var logPane: some View {
        ScrollViewReader { proxy in
            List {
                ForEach(model.logLines) { line in
                    Text(line.text)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(Color(white: 0.88))
                        .textSelection(.enabled)
                        .listRowBackground(Color.black)
                        .listRowSeparator(.hidden)
                        .listRowInsets(EdgeInsets(top: 1, leading: 12, bottom: 1, trailing: 12))
                        .id(line.id)
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(Color.black)
            .simultaneousGesture(
                DragGesture().onChanged { _ in
                    if !model.isPausedAutoscroll { model.setPaused(true) }
                }
            )
            .onChange(of: model.logLines.last?.id) { _, lastId in
                guard let lastId, !model.isPausedAutoscroll else { return }
                withAnimation(.linear(duration: 0.15)) { proxy.scrollTo(lastId, anchor: .bottom) }
            }
            .overlay(alignment: .bottomTrailing) {
                if model.isPausedAutoscroll, model.newLineCount > 0 {
                    Button {
                        model.resumeAutoscroll()
                        if let lastId = model.logLines.last?.id {
                            withAnimation { proxy.scrollTo(lastId, anchor: .bottom) }
                        }
                    } label: {
                        Label("\(model.newLineCount) new", systemImage: "arrow.down")
                            .font(.caption)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .background(Capsule().fill(MobaideColors.stateRunning))
                            .foregroundStyle(.white)
                    }
                    .padding()
                }
            }
        }
    }

    private func phaseIcon(_ status: String) -> String {
        switch status {
        case "ok": return "checkmark.circle.fill"
        case "active": return "arrow.triangle.2.circlepath"
        case "failed": return "xmark.circle.fill"
        case "skipped": return "minus.circle"
        default: return "circle"
        }
    }
}
