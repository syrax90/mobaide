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

    init(container: AppContainer) {
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
            .safeAreaInset(edge: .top, spacing: 0) {
                if model.showExternalSessionsNotice {
                    ExternalSessionsNotice { model.dismissExternalSessionsNotice() }
                }
            }
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
            .task {
                for await id in model.autoResumeEvents() {
                    router.navigate(to: .sessionDetail(sessionId: id))
                    model.clearAutoResume()
                }
            }
            .onChange(of: model.error) { _, newValue in
                if let newValue {
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
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView("No active sessions", systemImage: "terminal")
            }
        } else {
            List {
                if !model.activeScans.isEmpty {
                    Section("Active Auto Scans") {
                        ForEach(model.activeScans, id: \.project_name) { scan in
                            AutoScanCard(
                                scan: scan,
                                onTap: { router.navigate(to: .autoScanDetail(projectName: scan.project_name)) },
                                onStop: { Task { await model.stopScan(scan.project_name) } }
                            )
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
                    }
                } header: {
                    if !model.activeScans.isEmpty, !model.sessions.isEmpty {
                        Text("Sessions")
                    }
                }
            }
            .listStyle(.plain)
        }
    }

    private var killPresented: Binding<Bool> {
        Binding(
            get: { pendingKill != nil },
            set: { if !$0 { pendingKill = nil } }
        )
    }
}

private struct SessionCardRow: View {
    let session: SessionResponse
    let kindLabel: String
    let onStop: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Circle()
                .fill(SessionDisplay.stateColor(session.state))
                .frame(width: 10, height: 10)

            VStack(alignment: .leading, spacing: 2) {
                Text(session.title ?? kindLabel)
                    .font(.body)
                Text(kindLabel)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let cwd = session.cwd {
                    Text(cwd)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.head)
                }
            }

            Spacer()

            if let attention = SessionDisplay.attention(session.attention) {
                Text(attention.text)
                    .font(.caption2)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Capsule().fill(attention.color.opacity(0.25)))
                    .foregroundStyle(attention.color)
            }

            if SessionDisplay.canStop(session.state) {
                Button(action: onStop) {
                    Image(systemName: "stop.circle")
                        .foregroundStyle(.red)
                }
                .buttonStyle(.borderless)
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }
}
