import SwiftUI

/// The `herdrPane` route: one Herdr pane, live. It is the terminal screen on
/// the pane's session, with the pane's own top-bar actions (the watch/control
/// switch, the history).
struct HerdrPaneScreen: View {
    let sessionId: String
    let serverId: String
    let paneId: String
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            HerdrPaneContent(container: container, sessionId: sessionId, serverId: serverId, paneId: paneId)
                // The content's `@State` models are seeded in `init`.
                .id(sessionId)
        }
    }
}

private struct HerdrPaneContent: View {
    @State private var pane: HerdrPaneModel
    /// The same model the terminal screen below draws: the store keeps one
    /// per session id.
    @State private var terminal: TerminalSessionModel
    private let container: AppContainer
    private let sessionId: String
    private let paneKind: SessionKind
    private let route: Route

    @Environment(Router.self) private var router

    init(container: AppContainer, sessionId: String, serverId: String, paneId: String) {
        self.container = container
        self.sessionId = sessionId
        route = .herdrPane(sessionId: sessionId, serverId: serverId, paneId: paneId)
        let found = HerdrDisplay.findPane(
            in: container.herdrRepository.currentSnapshot, serverId: serverId, paneId: paneId
        )
        // The key row follows the agent in the pane; a plain terminal otherwise.
        let kind = found?.pane.agent.map(SessionKind.init(wire:)) ?? .shell
        paneKind = kind
        let terminal = container.terminalSessionStore.model(
            id: sessionId, kind: kind, resolvedKind: kind, initialStatus: nil
        )
        // Before its first frame: a pane view sends no size of its own until
        // the agent names it as the controller.
        terminal.isHerdrPane = true
        _terminal = State(initialValue: terminal)
        _pane = State(initialValue: HerdrPaneModel(
            serverId: serverId,
            paneId: paneId,
            repository: container.herdrRepository,
            seenStore: container.herdrSeenStore,
            foregroundSessionTracker: container.foregroundSessionTracker,
            removeAlert: { [service = container.notificationService] serverId, paneId in
                service.removeHerdr(serverId: serverId, paneId: paneId)
            }
        ))
    }

    /// Control belongs to one client; another device's control leaves this
    /// one a watcher.
    private var controlling: Bool { terminal.paneState?.isControl == true }

    private var notice: String? {
        if controlling {
            return "You control this pane. It has the size of this screen, also on your computer."
        }
        if terminal.paneState?.isControlledElsewhere == true {
            return "Another device controls this pane. It has the size of that device."
        }
        if !pane.typeInWatch {
            return "Watching. Use the message box, or take control to type freely."
        }
        return nil
    }

    var body: some View {
        TerminalScreenContent(
            container: container,
            sessionId: sessionId,
            stoppedSessionId: nil,
            onSwitchAfterStop: { _, _ in },
            pane: TerminalPaneChrome(
                title: pane.title,
                notice: notice,
                canControl: pane.canControl,
                controlling: controlling,
                controlBusy: pane.controlBusy,
                onToggleControl: { pane.setControl(!controlling) },
                onOpenHistory: { pane.openHistory() }
            ),
            paneKind: paneKind
        )
        .sheet(isPresented: Bindable(pane).showsHistory) {
            HerdrHistorySheet(title: pane.title, state: pane.history ?? .loading) {
                pane.openHistory()
            }
        }
        .task { await pane.observe() }
        .onChange(of: controlling, initial: true) { _, isControl in
            pane.modeChanged(isControl: isControl)
        }
        .onChange(of: terminal.sessionStatus, initial: true) { _, status in
            switch status {
            case "stopped": pane.sessionStopped()
            case "running": pane.sessionRunning()
            default: break
            }
        }
        .onChange(of: pane.event) { _, event in
            guard let event else { return }
            pane.event = nil
            switch event {
            case .showMessage(let text):
                terminal.terminalMessage = ToastMessage(text: text, tone: .danger)
            case .reattach:
                terminal.reattach()
            case .paneGone:
                if router.path.last == route { router.pop() }
            }
        }
        .onAppear { pane.markScreenVisible() }
        .onDisappear {
            pane.markScreenHidden()
            // Covered by another screen, the pane stays open. Off the stack,
            // it is given back and its stream ends now: nobody watches it.
            guard !router.path.contains(route) else { return }
            pane.leave()
            terminal.suspendStream()
            // The model is dropped later, once this view is gone: a render of
            // the leaving view would ask the store for it and get a new one,
            // which would attach to the pane again.
            Task { @MainActor [router, route, sessionId, store = container.terminalSessionStore] in
                try? await Task.sleep(for: .seconds(2))
                // Opened again meanwhile: it is in use.
                if !router.path.contains(route) { store.evict(sessionId) }
            }
        }
    }
}

/// The pane's recent output as plain text. The live view shows only the
/// screen, so this is how earlier output is read and copied.
struct HerdrHistorySheet: View {
    let title: String
    let state: HerdrHistoryState
    let onRefresh: () -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(\.mobaideColors) private var colors

    var body: some View {
        NavigationStack {
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .pixelScreenBackground()
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Close") { dismiss() }
                    }
                    ToolbarItemGroup(placement: .confirmationAction) {
                        if case .loaded(let text, _) = state, !text.isEmpty {
                            Button {
                                Clipboard.copy(text)
                            } label: {
                                Image(systemName: "doc.on.doc")
                            }
                            .accessibilityLabel("Copy all")
                        }
                        Button(action: onRefresh) {
                            Image(systemName: "arrow.clockwise")
                        }
                        .disabled(state == .loading)
                        .accessibilityLabel("Refresh")
                    }
                }
        }
        .pixelSheet()
    }

    @ViewBuilder
    private var content: some View {
        switch state {
        case .loading:
            Text("Reading the pane…")
                .font(.footnote)
                .foregroundStyle(colors.textMuted)
                .padding(16)
        case .failed(let message):
            PixelErrorBanner(message)
                .padding(16)
        case .loaded(let text, let truncated):
            VStack(alignment: .leading, spacing: 0) {
                if truncated {
                    Text("Older lines exist. This is the last part.")
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                        .padding(EdgeInsets(top: 8, leading: 16, bottom: 0, trailing: 16))
                }
                // Two nested one-axis scrolls: a two-axis `ScrollView` centres
                // content that is shorter than the sheet.
                ScrollView(.vertical) {
                    ScrollView(.horizontal) {
                        Text(text.isEmpty ? "The pane is empty." : text)
                            .font(MobaideTypography.code)
                            .foregroundStyle(colors.text)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: true, vertical: true)
                            .padding(16)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                // A terminal's news is at its end, so the sheet opens there.
                .defaultScrollAnchor(.bottom)
                .accessibilityIdentifier("herdr-history-text")
            }
        }
    }
}
