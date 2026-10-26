import SwiftUI

/// Terminal screen (Android `TerminalScreen`), the real body for the
/// `sessionDetail` route. The session model is pulled from the app-scoped
/// `TerminalSessionStore` so the emulator/connection survive this view being
/// recreated.
struct SessionDetailScreen: View {
    let sessionId: String
    @Environment(\.appContainer) private var container
    @Environment(Router.self) private var router
    /// Outlives the in-place route swap (this view's identity is reused), so
    /// the next terminal can open with the drawer already up.
    @State private var handOff: StopHandOff?

    var body: some View {
        if let container {
            TerminalScreenContent(
                container: container,
                sessionId: sessionId,
                stoppedSessionId: handOff?.nextId == sessionId ? handOff?.stoppedId : nil,
                onSwitchAfterStop: { next, stopped in
                    handOff = StopHandOff(stoppedId: stopped, nextId: next)
                    router.replaceLast(with: .sessionDetail(sessionId: next))
                }
            )
            // The sessions drawer swaps the route in place; the content's
            // `@State` model is seeded in `init`, so a reused identity would
            // keep rendering the previous session.
            .id(sessionId)
        }
    }
}

/// A stopped session on screen handing its terminal to the nearest open one.
private struct StopHandOff: Equatable {
    let stoppedId: String
    let nextId: String
}

private struct TerminalScreenContent: View {
    @State private var model: TerminalSessionModel
    @State private var keyboard = TerminalKeyboardObserver()
    @State private var showTemplates = false
    @State private var showSaveTemplate = false
    @State private var templateName = ""
    @State private var showShortcutPicker = false
    @State private var showFullScreenInput = false
    /// Set by the expanded editor's Send. Dismissing a sheet hands first
    /// responder back to the inline composer that held it at presentation, so
    /// without this the keyboard pops straight back up after a send.
    @State private var releaseComposerFocusOnDismiss = false
    /// Tracks `onAppear`/`onDisappear` so the keyboard observer is only ever
    /// restarted onto a screen that is still mounted (see `showFullScreenInput`
    /// below).
    @State private var screenVisible = false
    @State private var renameShortcutTarget: SavedShortcutUi?
    /// `capabilities.git` from one `GET /api/version` per screen visit; the
    /// menu's Commit… exists only on an agent that advertises `commit`,
    /// Fetch / Pull / Push… on any of the three job keys.
    @State private var gitCapabilities = GitCapabilities.none
    /// Last known row for this session, the header label's source. Held rather
    /// than read from the repository on demand because `SessionRepository` is
    /// not `@Observable` — only its stream drives a re-render.
    @State private var sessionRow: SessionResponse?
    @State private var customTypeNames: [String: String]
    /// Voice input for the connected agent: the composer's mic and dictation.
    @State private var voice: VoiceInputModel
    @State private var panel: SessionsPanelModel
    @State private var drawerOpen: Bool
    @State private var pendingStop: SessionResponse?
    /// Set while the panel hands the selection from this stopped session to
    /// the next one, before the terminals swap.
    @State private var handOff: StopHandOff?
    /// The terminal fades out before a stop hand-off and the next one fades in.
    @State private var terminalOpacity: Double
    /// Live translation of the terminal's left-edge pan while the finger is
    /// down; nil otherwise. `SideDrawer` follows it before the open verdict.
    @State private var drawerDrag: CGFloat?
    private let displayStore: TerminalDisplayStore
    private let sessionRepository: SessionRepository
    private let customAgentTypesRepository: CustomAgentTypesRepository
    private let agentAPI: AgentAPI
    private let sessionId: String
    /// Set when this terminal is the target of a stop hand-off.
    private let stoppedSessionId: String?
    private let onSwitchAfterStop: (_ next: String, _ stopped: String) -> Void

    @Environment(Router.self) private var router
    @Environment(DeepLinkCoordinator.self) private var deepLink
    @Environment(\.mobaideColors) private var colors
    @Environment(\.scenePhase) private var scenePhase

    private static let handOffFade = Animation.easeInOut(duration: 0.25)

    init(
        container: AppContainer,
        sessionId: String,
        stoppedSessionId: String?,
        onSwitchAfterStop: @escaping (_ next: String, _ stopped: String) -> Void
    ) {
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
        _voice = State(initialValue: VoiceInputModel(container: container, sessionId: sessionId))
        _customTypeNames = State(initialValue: Self.typeNames(container.customAgentTypesRepository.currentTypes))
        _panel = State(initialValue: SessionsPanelModel(
            sessionRepository: container.sessionRepository,
            agentEventBus: container.agentEventBus,
            customAgentTypesRepository: container.customAgentTypesRepository,
            agentRepository: container.agentRepository,
            connectedAgentId: container.tokenStore.getAgentId()
        ))
        displayStore = container.terminalDisplayStore
        sessionRepository = container.sessionRepository
        customAgentTypesRepository = container.customAgentTypesRepository
        agentAPI = container.agentAPI
        self.sessionId = sessionId
        self.stoppedSessionId = stoppedSessionId
        self.onSwitchAfterStop = onSwitchAfterStop
        _drawerOpen = State(initialValue: stoppedSessionId != nil)
        _terminalOpacity = State(initialValue: stoppedSessionId != nil ? 0 : 1)
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

    private var stopPresented: Binding<Bool> {
        Binding(
            get: { pendingStop != nil },
            set: { if !$0 { pendingStop = nil } }
        )
    }

    /// `checkSessionStillExists` never leaves a session this screen has seen
    /// alive, so stopping the one on screen navigates away here instead — to
    /// the nearest open session, or the list when none is left — unless the
    /// user switched sessions while the request was in flight.
    private func stopSession(_ id: String, force: Bool) async {
        let stopped: SessionsPanelModel.StoppedSession
        do {
            guard let result = try await panel.stopSession(id, force: force) else { return }
            stopped = result
        } catch {
            model.terminalMessage = ToastMessage(text: "Failed to stop session: \(String(describing: error))", tone: .danger)
            return
        }
        if id == sessionId {
            guard router.path.last == .sessionDetail(sessionId: id) else { return }
            guard let next = stopped.nextSessionId else {
                drawerOpen = false
                router.popTo(.sessionList)
                return
            }
            // The panel moves its selection while this terminal fades out; the
            // swap follows unless the user left meanwhile.
            withAnimation(Self.handOffFade) {
                handOff = StopHandOff(stoppedId: id, nextId: next)
                terminalOpacity = 0
            }
            try? await Task.sleep(for: .milliseconds(250))
            guard router.path.last == .sessionDetail(sessionId: id) else {
                // Something else was opened on top: this screen must still show
                // its (stopped) terminal when the user comes back to it.
                handOff = nil
                terminalOpacity = 1
                return
            }
            if drawerOpen {
                onSwitchAfterStop(next, id)
            } else {
                router.replaceLast(with: .sessionDetail(sessionId: next))
            }
        } else {
            model.terminalMessage = ToastMessage(text: "Session stopped", tone: .ok)
        }
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
                        keyboardTransition: keyboard.transition,
                        onDrawerEdgePan: handleDrawerEdgePan
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
                    onRenameSavedShortcut: { renameShortcutTarget = $0 },
                    voice: voice
                )
            }
            .offset(y: -keyboard.transition.lift)
            .opacity(terminalOpacity)
            connectionOverlay
            // Beside the overlay, outside the lifted column: the drawer must
            // not move with the keyboard. It is dismissed on open instead,
            // because `.ignoresSafeArea(.keyboard)` would let it cover the
            // panel's lower rows.
            SideDrawer(isOpen: $drawerOpen, dragTranslation: drawerDrag) {
                SessionsPanel(
                    model: panel,
                    currentSessionId: handOff?.nextId ?? sessionId,
                    departingSessionId: handOff?.stoppedId ?? stoppedSessionId,
                    switchingAgentId: deepLink.isSwitchingAgent ? deepLink.pendingAgentId : nil,
                    onSelectSession: { id in
                        drawerOpen = false
                        router.replaceLast(with: .sessionDetail(sessionId: id))
                    },
                    onStopSession: { pendingStop = $0 },
                    onCreateSession: {
                        drawerOpen = false
                        router.navigate(to: .createSession)
                    },
                    onSelectAgent: { agent in
                        deepLink.openSessions(ofAgent: agent.agent_id)
                    },
                    onClose: { drawerOpen = false }
                )
            }
        }
        .clipped()
        .ignoresSafeArea(.keyboard, edges: .bottom)
        // The left edge is the drawer's; the back button still pops.
        .background(InteractivePopGestureDisabler())
        .navigationTitle(headerLabel)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button {
                    openDrawer()
                } label: {
                    Image(systemName: "sidebar.left")
                }
                .accessibilityLabel("Sessions")
                .accessibilityIdentifier("terminal-sessions-button")
            }
            ToolbarItemGroup(placement: .topBarTrailing) {
                PixelStatusSquare(tone: SessionDisplay.stateTone(model.sessionStatus ?? ""), size: 10)
                    .accessibilityLabel(model.sessionStatus ?? "unknown")
                // Session-scoped git needs a directory to run in, and the row
                // is nil until it loads (cold-start deep link), so the menu
                // appears once the cwd is known. Fixed order: a top-bar `Menu`
                // otherwise renders its sections bottom-up. Branches… links to
                // the project's Git screen, so it also needs a project-backed
                // session.
                if sessionRow?.cwd != nil {
                    Menu {
                        Section("Git") {
                            Button {
                                router.navigate(to: .sessionGitChanges(sessionId: sessionId))
                            } label: {
                                Label("Changes…", systemImage: "arrow.triangle.branch")
                            }
                            if gitCapabilities.commit {
                                Button {
                                    router.navigate(to: .sessionGitChanges(sessionId: sessionId, commit: true))
                                } label: {
                                    Label("Commit…", systemImage: "checkmark.circle")
                                }
                            }
                            if gitCapabilities.hasSync {
                                Button {
                                    router.navigate(to: .sessionGitChanges(sessionId: sessionId, sync: true))
                                } label: {
                                    Label("Fetch / Pull / Push…", systemImage: "arrow.triangle.2.circlepath")
                                }
                            }
                            if gitCapabilities.branches, let projectName = sessionRow?.project_name {
                                Button {
                                    router.navigate(to: .gitManagement(projectName: projectName))
                                } label: {
                                    Label("Branches…", systemImage: "point.topleft.down.to.point.bottomright.curvepath")
                                }
                            }
                            if gitCapabilities.rebase {
                                Button {
                                    router.navigate(to: .sessionGitChanges(sessionId: sessionId, rebase: true))
                                } label: {
                                    Label("Rebase…", systemImage: "arrow.triangle.merge")
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
        .sheet(isPresented: $showFullScreenInput, onDismiss: {
            if releaseComposerFocusOnDismiss {
                releaseComposerFocusOnDismiss = false
                model.dismissSoftKeyboard()
            }
        }) {
            FullScreenInputSheet(model: model, voice: voice) {
                releaseComposerFocusOnDismiss = true
            }
        }
        .sheet(isPresented: Bindable(voice).setupVisible, onDismiss: { voice.hideSetup() }) {
            VoiceSetupSheet(voice: voice)
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
        .confirmationDialog(
            "Stop session?",
            isPresented: stopPresented,
            presenting: pendingStop
        ) { session in
            Button("Stop", role: .destructive) {
                Task { await stopSession(session.session_id, force: false) }
            }
            Button("Force Stop", role: .destructive) {
                Task { await stopSession(session.session_id, force: true) }
            }
            Button("Cancel", role: .cancel) {}
        } message: { session in
            Text("Stop “\(SessionDisplay.dialogLabel(session, customTypeNames: panel.customTypeNames))”?")
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
        .task { await panel.observeSessions() }
        .task { await panel.observeSessionsSynced() }
        .task { await panel.observeConnection() }
        .task { await panel.observeCustomTypes() }
        .task { await panel.observeAgents() }
        .task { await panel.refreshAgents() }
        .onChange(of: drawerOpen) { _, open in
            if open { model.dismissSoftKeyboard() }
        }
        .task {
            gitCapabilities = (try? await agentAPI.getAgentVersion())?.gitCapabilities ?? .none
        }
        .task {
            // The transcript goes into the composer for review, never sent.
            voice.onTranscript = { [model] text in model.insertTranscript(text) }
            await voice.load()
        }
        .task { await voice.watchConnection() }
        .onChange(of: voice.status?.state) { _, _ in voice.statusChanged() }
        // Leaving the foreground keeps what was said and transcribes it.
        // `.background` only: the microphone permission alert, Control Center
        // and a pulled-down notification all make the scene `.inactive`.
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { voice.interrupt() }
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
            if terminalOpacity < 1, handOff == nil {
                withAnimation(Self.handOffFade) { terminalOpacity = 1 }
            }
            screenVisible = true
            model.markVisible()
            keyboard.onKeyboardWillHide = { model.syncTerminalKeyboardState() }
            keyboard.start()
        }
        .onDisappear {
            screenVisible = false
            model.markHidden()
            keyboard.stop()
            voice.leave()
        }
    }

    private func openDrawer() {
        drawerDrag = nil
        drawerOpen = true
    }

    private func handleDrawerEdgePan(_ pan: DrawerEdgePan) {
        switch pan {
        case .changed(let translation):
            if !drawerOpen { drawerDrag = translation }
        case .ended(let translation, let velocity):
            drawerDrag = nil
            if SideDrawer<EmptyView>.shouldOpen(translation: translation, velocity: velocity) {
                drawerOpen = true
            }
        case .cancelled:
            drawerDrag = nil
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
