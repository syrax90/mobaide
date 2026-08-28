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
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.mobaideColors) private var colors

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
        .pixelScreenBackground()
        .navigationTitle(model.projectName)
        .navigationBarTitleDisplayMode(.inline)
        .toast($toast)
        // Keyed on the scene phase so backgrounding cancels `run()`, which
        // terminates the frame stream and closes the socket, and foregrounding
        // starts a fresh one. Nobody reads a log pane from a backgrounded app,
        // and a scan in its active phase streams every line it produces.
        //
        // Unlike the terminal this needs no byte budget: there is no ring
        // replay to amortise, because `run()` rebuilds its subscribe frame from
        // `lastSeq`, so the resume picks up at the high-water mark.
        // Keyed on `.background`, not on `.active`: `AppLifecycleController` —
        // which drives the terminal half of this same gate — deliberately
        // ignores `.inactive`, because a Control Center pull, the notification
        // shade or an app-switcher peek never suspends the app. Tearing the
        // stream down for those would cost a reconnect per gesture.
        .task(id: scenePhase != .background) {
            guard scenePhase != .background else { return }
            await model.run()
        }
        .task {
            for await error in model.errors() {
                toast = ToastMessage(text: error.message)
            }
        }
    }

    private var headerCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                PixelStatusSquare(tone: AutoScanDisplay.statusTone(model.statusText))
                Text(model.statusText.capitalized)
                    .font(.headline)
                    .foregroundStyle(colors.text)
                if model.cycleNumber > 0 {
                    Text("· cycle #\(model.cycleNumber)")
                        .font(.subheadline)
                        .foregroundStyle(colors.textMuted)
                }
                Spacer()
            }
            CountdownView(nextScanAt: model.nextScanAt)
            HStack(spacing: 12) {
                Button("Run Now") { Task { await model.runNow() } }
                    .buttonStyle(.pixelPrimary)
                Button("Stop") { Task { await model.stopScan() } }
                    .buttonStyle(.pixelDanger)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .pixelCard()
    }

    private var phaseTimeline: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(model.orderedPhases, id: \.name) { phase in
                HStack(spacing: 8) {
                    Image(systemName: phaseIcon(phase.state.status))
                        .foregroundStyle(colors.color(AutoScanDisplay.phaseTone(phase.state.status)))
                    Text(phase.name.capitalized)
                        .font(phase.name == model.activePhase ? .body.bold() : .body)
                        .foregroundStyle(colors.text)
                    Text(phase.state.status)
                        .chromeText()
                        .foregroundStyle(colors.color(AutoScanDisplay.phaseTone(phase.state.status)))
                    Spacer()
                }
            }
            if !model.recentIssues.isEmpty {
                Text("Recent issues")
                    .sectionHeading()
                    .padding(.top, 4)
                ForEach(Array(model.recentIssues.suffix(5).enumerated()), id: \.offset) { _, issue in
                    Text("\(issue.identifier): \(issue.title) (\(issue.step)/\(issue.status))")
                        .font(.caption)
                        .foregroundStyle(colors.color(AutoScanDisplay.issueTone(issue.status)))
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
                        .foregroundStyle(MobaideColors.logForeground)
                        .textSelection(.enabled)
                        .listRowBackground(MobaideColors.logBackground)
                        .listRowSeparator(.hidden)
                        .listRowInsets(EdgeInsets(top: 1, leading: 12, bottom: 1, trailing: 12))
                        .id(line.id)
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            // The log pane stays dark in both themes, like the terminal grid:
            // the ANSI palette is a matched set with its background.
            .background(MobaideColors.logBackground)
            .pixelRing(MobaideColors.logFrame)
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
                    }
                    .buttonStyle(.pixelSmallPrimary)
                    .padding()
                }
            }
        }
    }

    private func phaseIcon(_ status: String) -> String {
        switch status {
        case "ok": return "checkmark.square.fill"
        case "active": return "arrow.triangle.2.circlepath"
        case "failed": return "xmark.square.fill"
        case "skipped": return "minus.square"
        default: return "square"
        }
    }
}
