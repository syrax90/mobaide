import SwiftUI

/// Sessions list (Android `SessionListScreen`), the real body for the
/// `sessionList` route. Cards render state + attention; the list auto-refreshes
/// from bus events and offers pull-to-refresh (a fresh server snapshot).
struct SessionListScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            SessionListContent(container: container)
        }
    }
}

private struct SessionListContent: View {
    @State private var model: SessionListModel
    @State private var toast: ToastMessage?
    @State private var pendingKill: SessionResponse?

    @Environment(Router.self) private var router

    private let reviewPrompt: ReviewPromptCoordinator

    init(container: AppContainer) {
        reviewPrompt = container.reviewPromptCoordinator
        _model = State(initialValue: SessionListModel(
            repository: container.sessionRepository,
            agentEventBus: container.agentEventBus,
            preferencesStore: container.preferencesStore,
            autoScanRepository: container.autoScanRepository,
            customAgentTypesRepository: container.customAgentTypesRepository
        ))
    }

    var body: some View {
        content
            .navigationTitle("Sessions")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        router.navigate(to: .createSession)
                    } label: {
                        Image(systemName: "plus")
                    }
                }
            }
            .refreshable { await model.refresh() }
            .confirmationDialog(
                "Stop session?",
                isPresented: killPresented,
                presenting: pendingKill
            ) { session in
                Button("Stop", role: .destructive) {
                    Task { await model.deleteSession(session.session_id, force: false) }
                }
                Button("Force Stop", role: .destructive) {
                    Task { await model.deleteSession(session.session_id, force: true) }
                }
                Button("Cancel", role: .cancel) {}
            } message: { session in
                Text("Stop “\(SessionDisplay.dialogLabel(session, customTypeNames: model.customTypeNames))”?")
            }
            .toast($toast)
            .task { await model.observeSessions() }
            .task { await model.observeSessionsSynced() }
            .task { await model.observeEndedSessionNotices() }
            .task { await model.observeConnection() }
            .task { await model.observeScans() }
            .task { await model.observeCustomTypes() }
            .task { await reviewPrompt.sessionListShown() }
            .task {
                for await id in model.autoResumeEvents() {
                    router.navigate(to: .sessionDetail(sessionId: id))
                    model.clearAutoResume()
                }
            }
            .onChange(of: model.error) { _, newValue in
                if let newValue {
                    reviewPrompt.noteError()
                    toast = ToastMessage(text: newValue)
                    model.clearError()
                }
            }
            .onChange(of: model.notice) { _, newValue in
                if let newValue {
                    toast = ToastMessage(text: newValue)
                    model.clearNotice()
                }
            }
    }

    @ViewBuilder
    private var content: some View {
        if model.sessions.isEmpty, model.activeScans.isEmpty {
            if model.isReconnecting {
                LoadingIndicator()
                    .pixelScreenBackground()
            } else {
                ScrollView {
                    PixelEmptyState("No active sessions", systemImage: "terminal")
                        .padding(16)
                }
                .pixelScreenBackground()
            }
        } else {
            List {
                if !model.activeScans.isEmpty {
                    Section(header: PixelSectionHeader("Active Auto Scans")) {
                        ForEach(model.activeScans, id: \.project_name) { scan in
                            AutoScanCard(
                                scan: scan,
                                onTap: { router.navigate(to: .autoScanDetail(projectName: scan.project_name)) },
                                onStop: { Task { await model.stopScan(scan.project_name) } }
                            )
                            .pixelCard()
                            .pixelListRow()
                        }
                    }
                }
                Section {
                    ForEach(model.sessions, id: \.session_id) { session in
                        Button {
                            router.navigate(to: .sessionDetail(sessionId: session.session_id))
                        } label: {
                            SessionCardRow(
                                session: session,
                                kindLabel: SessionDisplay.kindLabel(session, customTypeNames: model.customTypeNames)
                            ) { pendingKill = session }
                        }
                        .buttonStyle(.plain)
                        .pixelCard()
                        .pixelListRow()
                    }
                } header: {
                    if !model.activeScans.isEmpty, !model.sessions.isEmpty {
                        PixelSectionHeader("Sessions")
                    }
                }
            }
            .pixelList()
        }
    }

    private var killPresented: Binding<Bool> {
        Binding(
            get: { pendingKill != nil },
            set: { if !$0 { pendingKill = nil } }
        )
    }
}

/// Web `.session-card`: status square, title / kind / cwd, attention chip,
/// and a stop control that is a sibling of the row's tap target.
private struct SessionCardRow: View {
    let session: SessionResponse
    let kindLabel: String
    let onStop: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack(spacing: 12) {
            SessionStatusIndicator(session: session)

            VStack(alignment: .leading, spacing: 3) {
                Text(session.title ?? kindLabel)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(colors.text)
                Text(kindLabel)
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
                if let cwd = session.cwd {
                    Text(cwd)
                        .font(.caption2)
                        .foregroundStyle(colors.textMuted)
                        .lineLimit(1)
                        .truncationMode(.head)
                }
            }

            Spacer()

            if let attention = SessionDisplay.statusChip(session) {
                PixelChip(attention.text, .status(attention.tone))
                    .fixedSize()
            }

            if SessionDisplay.canStop(session.state) {
                Button(action: onStop) {
                    Image(systemName: "stop.fill")
                        .foregroundStyle(colors.danger)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Stop session")
            }
        }
        .contentShape(Rectangle())
    }
}
