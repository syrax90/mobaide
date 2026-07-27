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
    private let displayStore: TerminalDisplayStore

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
        displayStore = container.terminalDisplayStore
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
        .toast(Bindable(model).shortcutMessage)
        .renameShortcutAlert(target: $renameShortcutTarget) { shortcut, name in
            model.renameShortcut(id: shortcut.id, name: name)
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
