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
    @State private var showFullScreenInput = false
    /// Tracks `onAppear`/`onDisappear` so the keyboard observer is only ever
    /// restarted onto a screen that is still mounted (see `showFullScreenInput`
    /// below).
    @State private var screenVisible = false
    @State private var renameShortcutTarget: SavedShortcutUi?
    /// `capabilities.git.commit` from one `GET /api/version` per screen visit;
    /// the menu's Commit… exists only on an agent that advertises it.
    @State private var gitCommitAvailable = false
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
    @Environment(\.mobaideColors) private var colors

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
                    onExpand: { showFullScreenInput = true },
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
            ToolbarItemGroup(placement: .topBarTrailing) {
                PixelStatusSquare(tone: SessionDisplay.stateTone(model.sessionStatus ?? ""), size: 10)
                    .accessibilityLabel(model.sessionStatus ?? "unknown")
                // Session-scoped git needs a directory to run in, and the row
                // is nil until it loads (cold-start deep link), so the menu
                // appears once the cwd is known. Fixed order: a top-bar `Menu`
                // otherwise renders its sections bottom-up, and the later git
                // actions (fetch / pull / push, branches, rebase) are reserved
                // slots under the same heading, after Commit…, in that order.
                if sessionRow?.cwd != nil {
                    Menu {
                        Section("Git") {
                            Button {
                                router.navigate(to: .sessionGitChanges(sessionId: sessionId))
                            } label: {
                                Label("Changes…", systemImage: "arrow.triangle.branch")
                            }
                            if gitCommitAvailable {
                                Button {
                                    router.navigate(to: .sessionGitChanges(sessionId: sessionId, commit: true))
                                } label: {
                                    Label("Commit…", systemImage: "checkmark.circle")
                                }
                            }
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .menuOrder(.fixed)
                    .accessibilityLabel("More options")
                }
            }
        }
        .sheet(isPresented: $showTemplates) {
            TemplatesPickerSheet(model: model)
        }
        .sheet(isPresented: $showShortcutPicker) {
            ShortcutPickerSheet(model: model)
        }
        .sheet(isPresented: $showFullScreenInput) {
            FullScreenInputSheet(model: model)
        }
        // The keyboard observer listens on app-wide `UIResponder` notifications,
        // so the expanded editor's keyboard would otherwise drive this screen's
        // pan.
        .onChange(of: showFullScreenInput) { _, presented in
            if presented {
                keyboard.stop()
            } else if screenVisible {
                keyboard.start()
            }
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
            gitCommitAvailable = (try? await agentAPI.getAgentVersion())?.gitCapability("commit") ?? false
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
            screenVisible = true
            model.markVisible()
            keyboard.onKeyboardWillHide = { model.syncTerminalKeyboardState() }
            keyboard.start()
        }
        .onDisappear {
            screenVisible = false
            model.markHidden()
            keyboard.stop()
        }
    }

    @ViewBuilder
    private var connectionOverlay: some View {
        switch model.connectionState {
        case .live:
            EmptyView()
        case .reconnecting:
            overlayChip("Reconnecting…")
        case .replayingBuffer:
            overlayChip("Replaying buffer…", emphasised: true)
        case .closed:
            overlayChip("Session ended")
        case .suspended:
            // Rarely on screen: coming back to the terminal resumes the stream
            // in the same pass that renders this. It exists so the state stays
            // honest for the frame in between, and so the send gates read a
            // suspend as "not live" rather than as a dropped connection.
            overlayChip("Paused")
        }
    }

    /// Web `.terminal-overlay`: a ringed `surface` chip over the grid; the
    /// replay state takes the pending (`brand2` / `accent`) tone.
    private func overlayChip(_ text: String, emphasised: Bool = false) -> some View {
        Text(text)
            .chromeText()
            .foregroundStyle(emphasised ? colors.accent : colors.text)
            .padding(EdgeInsets(top: 6, leading: 14, bottom: 6, trailing: 14))
            .background(colors.surface.opacity(0.92))
            .pixelRing(emphasised ? MobaideColorScheme.brand2 : colors.rule)
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
                        Image(systemName: "arrow.down")
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundStyle(colors.text)
                            .frame(width: 40, height: 40)
                            .background(colors.bg2)
                            .pixelRing(colors.surface2)
                            .pixelShadow(MobaideColorScheme.btnShadow)
                    }
                    .buttonStyle(.plain)
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
        PixelChip(rows > 999 ? "999+" : "\(rows)", .filled)
            .accessibilityLabel("\(rows) unread rows")
    }
}
