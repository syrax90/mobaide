import SwiftUI

/// Terminal screen (Android `TerminalScreen`), the real body for the
/// `sessionDetail` route. The session model is pulled from the app-scoped
/// `TerminalSessionStore` so the emulator/connection survive this view being
/// recreated.
struct SessionDetailScreen: View {
    let sessionId: String
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            TerminalScreenContent(container: container, sessionId: sessionId)
        }
    }
}

private struct TerminalScreenContent: View {
    @State private var model: TerminalSessionModel
    @State private var showTemplates = false
    @State private var showSaveTemplate = false
    @State private var templateName = ""

    init(container: AppContainer, sessionId: String) {
        let session = container.sessionRepository.currentSessions.first { $0.session_id == sessionId }
        _model = State(initialValue: container.terminalSessionStore.model(
            id: sessionId,
            kind: session?.kind ?? .claude,
            initialStatus: session?.state
        ))
    }

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .top) {
                TerminalHostView(model: model)
                connectionOverlay
                jumpToBottomButton
            }
            InputBar(
                model: model,
                onOpenTemplates: { showTemplates = true },
                onSaveTemplate: {
                    templateName = ""
                    showSaveTemplate = true
                }
            )
        }
        .navigationTitle(model.sessionId)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Circle()
                    .fill(SessionDisplay.stateColor(model.sessionStatus ?? ""))
                    .frame(width: 10, height: 10)
                    .accessibilityLabel(model.sessionStatus ?? "unknown")
            }
        }
        .sheet(isPresented: $showTemplates) {
            TemplatesPickerSheet(model: model)
        }
        .alert("Save as template", isPresented: $showSaveTemplate) {
            TextField("Name", text: $templateName)
            Button("Save") { model.saveInputAsTemplate(name: templateName) }
            Button("Cancel", role: .cancel) {}
        }
        .onAppear { model.markVisible() }
        .onDisappear { model.markHidden() }
    }

    @ViewBuilder
    private var connectionOverlay: some View {
        switch model.connectionState {
        case .live:
            EmptyView()
        case .reconnecting:
            overlayPill("Reconnecting…", background: MobaideColors.terminalOverlayReconnecting)
        case .replayingBuffer:
            overlayPill("Replaying buffer…", background: MobaideColors.terminalOverlayReplaying)
        case .closed:
            overlayPill("Session ended", background: MobaideColors.terminalOverlayReconnecting)
        }
    }

    private func overlayPill(_ text: String, background: Color) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(.white)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(Capsule().fill(background.opacity(0.85)))
            .padding(.top, 8)
    }

    @ViewBuilder
    private var jumpToBottomButton: some View {
        if !model.followMode {
            VStack {
                Spacer()
                HStack {
                    Spacer()
                    Button {
                        model.jumpToBottom()
                    } label: {
                        Image(systemName: "arrow.down.circle.fill")
                            .font(.system(size: 34))
                            .symbolRenderingMode(.hierarchical)
                    }
                    .padding(16)
                    .overlay(alignment: .topTrailing) {
                        if model.hasUnread {
                            Circle().fill(MobaideColors.warningYellow).frame(width: 10, height: 10)
                        }
                    }
                }
            }
        }
    }
}
