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
    @State private var keyboard = TerminalKeyboardObserver()
    @State private var showTemplates = false
    @State private var showSaveTemplate = false
    @State private var templateName = ""
    @State private var showShortcutPicker = false
    @State private var renameShortcutTarget: SavedShortcutUi?
    /// Last known row for this session, the header label's source. Held rather
    /// than read from the repository on demand because `SessionRepository` is
    /// not `@Observable` — only its stream drives a re-render.
    @State private var sessionRow: SessionResponse?
    @State private var customTypeNames: [String: String]
    private let displayStore: TerminalDisplayStore
    private let sessionRepository: SessionRepository
    private let customAgentTypesRepository: CustomAgentTypesRepository
    private let agentAPI: AgentAPI
    private let sessionId: String

    @Environment(Router.self) private var router
    @Environment(\.scenePhase) private var scenePhase

    init(container: AppContainer, sessionId: String) {
        let session = container.sessionRepository.currentSessions.first { $0.session_id == sessionId }
        _model = State(initialValue: container.terminalSessionStore.model(
            id: sessionId,
            kind: session?.kind ?? .claude,
            // nil when the session row is not loaded yet (cold-start deep
            // link): the model resolves the true kind itself rather than
            // trusting the `.claude` render fallback above.
            resolvedKind: session?.kind,
            initialStatus: session?.state
        ))
        _sessionRow = State(initialValue: session)
        _customTypeNames = State(initialValue: Self.typeNames(container.customAgentTypesRepository.currentTypes))
        displayStore = container.terminalDisplayStore
        sessionRepository = container.sessionRepository
        customAgentTypesRepository = container.customAgentTypesRepository
        agentAPI = container.agentAPI
        self.sessionId = sessionId
    }

    /// Empty only until the row resolves from either source below.
    private var headerLabel: String {
        sessionRow.map { SessionDisplay.headerLabel($0, customTypeNames: customTypeNames) } ?? ""
    }

    /// One-shot seed for a cold-start deep link (notification tap), whose
    /// `currentSessions` is empty and whose event bus may be slow or down while
    /// the PTY socket connects fine — without this the title stays blank for
    /// the whole visit.
    ///
    /// Read-only on purpose: `SessionRepository.listSessions()` publishes the
    /// REST snapshot and evicts terminal models absent from it, so a stale
    /// response on this cold-start path could disconnect a session created
    /// meanwhile (same reasoning as `AppContainer`'s kind resolver).
    private func seedSessionRow() async {
        guard sessionRow == nil else { return }
        let list = try? await agentAPI.listSessions()
        // Re-checked after the await: a snapshot that landed while the request
        // was in flight is newer than the response, and the title is mutable.
        guard sessionRow == nil else { return }
        sessionRow = list?.first { $0.session_id == sessionId }
    }

    private static func typeNames(_ types: [CustomAgentTypeDto]) -> [String: String] {
        Dictionary(types.map { ("custom_\($0.id)", $0.name) }, uniquingKeysWith: { _, last in last })
    }

    /// Leaves for the sessions list once the agent's snapshot proves this
    /// session is gone. Both the list and the synced flag can settle in either
    /// order, so both feed the same one-shot decision in the model.
    private func checkSessionStillExists() {
        let isListed = sessionRepository.currentSessions.contains { $0.session_id == sessionId }
        guard model.noteSessionSnapshot(
            isListed: isListed,
            synced: sessionRepository.sessionsSynced
        ) else { return }
        sessionRepository.notifySessionEnded(id: sessionId)
        router.popTo(.sessionList)
    }

    /// Pan-don't-resize keyboard layout (Android `ImeFloatingPan` parity):
    /// the screen opts out of SwiftUI keyboard avoidance, then lifts the whole
    /// column by the keyboard overlap as a placement-only offset — the
    /// terminal's allocated size is identical with the keyboard shown or
    /// hidden, so keyboard transitions never resize the grid (no PTY `r`
    /// frame). The wrapper counter-pans the terminal so the caret row stays
    /// visible; the outer ZStack does not move and clips the lifted content
    /// at the navigation-bar edge.
    var body: some View {
        ZStack(alignment: .top) {
            VStack(spacing: 0) {
                ZStack(alignment: .top) {
                    TerminalHostView(
                        model: model,
                        displayConfig: displayStore.config,
                        followerGrid: model.followerGrid,
                        keyboardTransition: keyboard.transition
                    )
                    jumpToBottomButton
                    selectionToolbar
                }
                InputBar(
                    model: model,
                    onOpenTemplates: { showTemplates = true },
                    onSaveTemplate: {
                        templateName = ""
                        showSaveTemplate = true
                    },
                    onOpenShortcuts: {
                        model.refreshShortcutRecents()
                        model.refreshSavedShortcuts()
                        showShortcutPicker = true
                    },
                    onRenameSavedShortcut: { renameShortcutTarget = $0 }
                )
            }
            .offset(y: -keyboard.transition.lift)
            connectionOverlay
        }
        .clipped()
        .ignoresSafeArea(.keyboard, edges: .bottom)
        .navigationTitle(headerLabel)
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
        .sheet(isPresented: $showShortcutPicker) {
            ShortcutPickerSheet(model: model)
        }
        .alert("Save as template", isPresented: $showSaveTemplate) {
            TextField("Name", text: $templateName)
            Button("Save") { model.saveInputAsTemplate(name: templateName) }
            Button("Cancel", role: .cancel) {}
        }
        // Failure surface for control-row shortcut actions (long-press
        // Rename/Remove) taken while the picker sheet is not up; the sheet
        // attaches its own copies of both for actions taken inside it.
        .toast(Bindable(model).terminalMessage)
        .renameShortcutAlert(target: $renameShortcutTarget) { shortcut, name in
            model.renameShortcut(id: shortcut.id, name: name)
        }
        .task {
            await seedSessionRow()
        }
        .task {
            checkSessionStillExists()
            for await sessions in sessionRepository.sessions() {
                // Last non-nil wins: a session dropping out of the snapshot
                // pops this screen, so blanking the header on the way out
                // would only flash an empty title bar.
                if let row = sessions.first(where: { $0.session_id == sessionId }) {
                    sessionRow = row
                }
                checkSessionStillExists()
            }
        }
        .task {
            for await _ in sessionRepository.sessionsSyncedStates() {
                checkSessionStillExists()
            }
        }
        // Only reachable for a custom-kind session whose row predates the
        // `kind_label` snapshot; the registry is the label's second chance.
        .task {
            try? await customAgentTypesRepository.refreshCustomTypes()
            for await types in customAgentTypesRepository.types() {
                customTypeNames = Self.typeNames(types)
            }
        }
        .onAppear {
            model.markVisible()
            keyboard.onKeyboardWillHide = { model.syncTerminalKeyboardState() }
            keyboard.start()
        }
        .onDisappear {
            model.markHidden()
            keyboard.stop()
        }
        // `onAppear`/`onDisappear` never fire for scene-level transitions on
        // an already-mounted screen; the takeover claim must still die on
        // backgrounding and re-arm on return (`.inactive` also cancels — the
        // app switcher and system overlays are not foreground attention).
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active:
                model.sceneWillActivate()
            case .inactive, .background:
                model.sceneDidBackground()
            @unknown default:
                break
            }
        }
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

    /// Sits in the terminal's own ZStack, so the anchor rect the selection
    /// controller reports in the host view's space maps straight to `.position`.
    private var selectionToolbar: some View {
        GeometryReader { proxy in
            TerminalSelectionToolbar(model: model, container: proxy.size)
        }
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
                    .accessibilityLabel("Jump to bottom")
                    .padding(16)
                    .overlay(alignment: .topTrailing) {
                        if model.hasUnread {
                            unreadBadge(model.unreadRows)
                        }
                    }
                }
            }
        }
    }

    /// Rows missed while paused (Android `JumpToBottomFab`'s badge). The label
    /// saturates well below the model's own cap so the pill keeps its size.
    private func unreadBadge(_ rows: Int) -> some View {
        Text(rows > 999 ? "999+" : "\(rows)")
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.black)
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(Capsule().fill(MobaideColors.warningYellow))
            .accessibilityLabel("\(rows) unread rows")
    }
}
