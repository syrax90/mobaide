import SwiftUI
import UIKit

/// The chat UI of a session: the terminal screen's chrome (sessions drawer,
/// title, status, `⋯` menu) over a structured timeline and a dock of pending
/// requests, plan, queue, config chips and the composer.
struct ChatScreen: View {
    @State private var model: ChatSessionModel
    @State private var voice: VoiceInputModel
    @State private var panel: SessionsPanelModel
    @State private var sessionRow: SessionResponse?
    @State private var customTypeNames: [String: String]
    @State private var gitCapabilities = GitCapabilities.none
    @State private var drawerOpen: Bool
    @State private var drawerDrag: CGFloat?
    @State private var pendingStop: SessionResponse?
    @State private var showTemplates = false
    @State private var showSaveTemplate = false
    @State private var templateName = ""
    @State private var showSessionInfo = false
    @State private var showFullScreenComposer = false
    @State private var focusView: Bool
    @State private var showSettings = false
    @State private var pickingConfig: ChatConfigOption?
    @State private var screenHeight: CGFloat = 0
    @FocusState private var signInFieldFocused: Bool

    private let container: AppContainer
    private let sessionId: String
    private let stoppedSessionId: String?
    private let onSwitchAfterStop: (_ next: String, _ stopped: String) -> Void

    @Environment(Router.self) private var router
    @Environment(DeepLinkCoordinator.self) private var deepLink
    @Environment(\.mobaideColors) private var colors
    @Environment(\.scenePhase) private var scenePhase

    init(
        container: AppContainer,
        sessionId: String,
        stoppedSessionId: String?,
        onSwitchAfterStop: @escaping (_ next: String, _ stopped: String) -> Void
    ) {
        self.container = container
        self.sessionId = sessionId
        self.stoppedSessionId = stoppedSessionId
        self.onSwitchAfterStop = onSwitchAfterStop
        _model = State(initialValue: container.chatSessionStore.model(id: sessionId))
        _voice = State(initialValue: VoiceInputModel(container: container, sessionId: sessionId))
        _sessionRow = State(initialValue: container.sessionRepository.currentSessions.first { $0.session_id == sessionId })
        _customTypeNames = State(initialValue: Self.typeNames(container.customAgentTypesRepository.currentTypes))
        _panel = State(initialValue: SessionsPanelModel(
            sessionRepository: container.sessionRepository,
            agentEventBus: container.agentEventBus,
            customAgentTypesRepository: container.customAgentTypesRepository,
            agentRepository: container.agentRepository,
            connectedAgentId: container.tokenStore.getAgentId()
        ))
        _drawerOpen = State(initialValue: stoppedSessionId != nil)
        _focusView = State(initialValue: container.preferencesStore.isChatFocusView())
    }

    // MARK: - Labels

    private var kindLabel: String {
        if let sessionRow { return SessionDisplay.kindLabel(sessionRow, customTypeNames: customTypeNames) }
        return model.state.agent?.displayName ?? "Agent"
    }

    private var title: String {
        if let title = model.state.title, !title.isBlank { return title }
        if let title = sessionRow?.title, !title.isBlank { return title }
        return kindLabel
    }

    private var cwd: String? { model.state.cwd ?? sessionRow?.cwd }

    private var statusTone: PixelTone {
        if model.sessionClosed || model.phase == .stopped { return .muted }
        if model.phase == .error { return .danger }
        return SessionDisplay.stateTone(sessionRow?.state ?? "starting")
    }

    // MARK: - Body

    var body: some View {
        ZStack(alignment: .top) {
            VStack(spacing: 0) {
                ChatTimelineView(
                    model: model,
                    agentLabel: model.state.agent?.displayName ?? kindLabel,
                    focusView: focusView
                )
                    .overlay(alignment: .leading) { edgePanStrip }
                // The timeline gives up its height first; inside the dock,
                // the request card's capped body gives way next.
                dock.layoutPriority(1)
            }
            if !model.isLive && !model.sessionClosed {
                ChatOverlayChip(text: "Reconnecting…")
            }
            SideDrawer(isOpen: $drawerOpen, dragTranslation: drawerDrag) {
                SessionsPanel(
                    model: panel,
                    currentSessionId: sessionId,
                    departingSessionId: stoppedSessionId,
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
            .ignoresSafeArea(.keyboard, edges: .bottom)
        }
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { screenHeight = $0 }
        .background(colors.bg)
        .background(InteractivePopGestureDisabler())
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { toolbar }
        .toast(Bindable(model).message)
        .sheet(isPresented: $showTemplates) {
            TemplatesPickerSheet(loadTemplates: loadTemplates, onPick: applyTemplate)
        }
        .sheet(isPresented: Bindable(voice).setupVisible, onDismiss: { voice.hideSetup() }) {
            VoiceSetupSheet(voice: voice)
        }
        .sheet(isPresented: $showFullScreenComposer) {
            ChatFullScreenComposer(model: model, voice: voice) {
                hideKeyboard()
            }
        }
        .sheet(isPresented: $showSessionInfo) {
            ChatSessionInfoSheet(
                agent: model.state.agent,
                cwd: cwd,
                acpSessionId: model.state.acpSessionId
            )
        }
        .sheet(isPresented: $showSettings) {
            ChatSettingsSheet(model: model)
        }
        .sheet(item: $pickingConfig) { option in
            ChatConfigPickerSheet(option: option, current: model.configValue(option)) { choice in
                model.setConfig(option, to: .string(choice.value))
            }
        }
        .alert("Save as template", isPresented: $showSaveTemplate) {
            TextField("Name", text: $templateName)
            Button("Save") { saveTemplate() }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog("Stop session?", isPresented: stopPresented, presenting: pendingStop) { session in
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
        .onChange(of: drawerOpen) { _, open in
            if open { hideKeyboard() }
        }
        .onChange(of: scenePhase) { _, phase in
            model.setAppActive(phase != .background)
            if phase == .background { voice.interrupt() }
        }
        .onChange(of: voice.status?.state) { _, _ in voice.statusChanged() }
        .onChange(of: voice.dictation.isActive) { _, active in
            UIApplication.shared.isIdleTimerDisabled = active
        }
        .task { await seedSessionRow() }
        .task { await panel.observeSessions() }
        .task { await panel.observeSessionsSynced() }
        .task { await panel.observeConnection() }
        .task { await panel.observeCustomTypes() }
        .task { await panel.observeAgents() }
        .task { await panel.refreshAgents() }
        .task {
            gitCapabilities = (try? await container.agentAPI.getAgentVersion())?.gitCapabilities ?? .none
        }
        .task {
            voice.onTranscript = { [model] text in model.insertTranscript(text) }
            voice.onNotice = { [model] text in model.message = ToastMessage(text: text, tone: .warning) }
            voice.sessionKind = sessionRow?.kind
            await voice.load()
        }
        .task { await voice.watchConnection() }
        .task {
            checkSessionStillExists()
            for await sessions in container.sessionRepository.sessions() {
                if let row = sessions.first(where: { $0.session_id == sessionId }) {
                    sessionRow = row
                    // Transcript cleanup is offered by session kind.
                    voice.sessionKind = row.kind
                }
                checkSessionStillExists()
            }
        }
        .task {
            for await _ in container.sessionRepository.sessionsSyncedStates() {
                checkSessionStillExists()
            }
        }
        .task {
            for await types in container.customAgentTypesRepository.types() {
                customTypeNames = Self.typeNames(types)
            }
        }
        .onAppear {
            model.setAppActive(scenePhase != .background)
            model.markVisible()
            container.notificationService.remove(sessionId: sessionId)
        }
        .onDisappear {
            model.markHidden()
            voice.leave()
            UIApplication.shared.isIdleTimerDisabled = false
        }
    }

    // MARK: - Top bar

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button {
                drawerDrag = nil
                drawerOpen = true
            } label: {
                Image(systemName: "sidebar.left")
            }
            .accessibilityLabel("Sessions")
            .accessibilityIdentifier("chat-sessions-button")
        }
        ToolbarItem(placement: .principal) {
            ChatTitleView(title: title, tone: statusTone, model: model)
        }
        ToolbarItemGroup(placement: .topBarTrailing) {
            if let usage = model.state.usage, usage.fraction != nil {
                ChatUsageIndicator(usage: usage)
            }
            Menu {
                if cwd != nil {
                    SessionGitMenuItems(
                        sessionId: sessionId,
                        projectName: sessionRow?.project_name,
                        capabilities: gitCapabilities,
                        navigate: { router.navigate(to: $0) }
                    )
                }
                Section {
                    Toggle(isOn: focusViewBinding) {
                        Label("Focus view", systemImage: "scope")
                    }
                    .accessibilityHint("Shows answers and questions only; the steps fold into one line")
                }
                Section {
                    if model.phase == .error, !model.sessionClosed {
                        Button {
                            model.restart()
                        } label: {
                            Label("Restart agent", systemImage: "arrow.clockwise")
                        }
                        .disabled(!model.isLive || model.restarting)
                    }
                    Button {
                        showSessionInfo = true
                    } label: {
                        Label("Session info", systemImage: "info.circle")
                    }
                    if let sessionRow, SessionDisplay.canStop(sessionRow.state) {
                        Button(role: .destructive) {
                            pendingStop = sessionRow
                        } label: {
                            Label("Close session", systemImage: "stop.circle")
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

    // MARK: - Dock

    private var dock: some View {
        VStack(spacing: 0) {
            if let request = model.state.requests.first {
                ChatRequestCard(
                    model: model,
                    request: request,
                    moreWaiting: model.state.requests.count - 1,
                    bodyCap: max(120, screenHeight * 0.3),
                    signInFieldFocused: $signInFieldFocused
                )
                .id(request.id)
                .padding(EdgeInsets(top: signInTyping ? 4 : 12, leading: 12, bottom: hasPlanOrQueue && !signInTyping ? 0 : 4, trailing: 12))
            }
            // While the sign-in code is typed, the card alone stays above
            // the keyboard: on a small phone there is no room for the rest.
            if !signInTyping {
                if hasPlanOrQueue {
                    ChatCappedScroll(maxHeight: planQueueCap) { planAndQueue }
                }
                configChips
                ChatComposer(
                    model: model,
                    voice: voice,
                    onOpenTemplates: { showTemplates = true },
                    onSaveTemplate: {
                        templateName = ""
                        showSaveTemplate = true
                    },
                    onExpand: { showFullScreenComposer = true }
                )
            }
        }
        .background(colors.surface)
        .overlay(alignment: .top) { PixelDivider() }
    }

    /// The focus flag alone could outlive the field, and the composer must
    /// never stay hidden without a code field on screen.
    private var signInTyping: Bool {
        guard signInFieldFocused, case .signIn(let signIn) = model.state.requests.first else { return false }
        return signIn.waiting && signIn.input != nil
    }

    private var hasPlanOrQueue: Bool {
        !model.state.plan.isEmpty || !model.state.queue.isEmpty
    }

    /// Smaller while a request card is shown, which takes its own space.
    private var planQueueCap: CGFloat {
        model.state.requests.isEmpty ? max(160, screenHeight * 0.55) : max(100, screenHeight * 0.2)
    }

    private var planAndQueue: some View {
        VStack(spacing: 10) {
            if !model.state.plan.isEmpty {
                ChatPlanBar(plan: model.state.plan)
            }
            if !model.state.queue.isEmpty {
                ChatQueueList(model: model)
            }
        }
        .padding(EdgeInsets(top: model.state.requests.isEmpty ? 12 : 10, leading: 12, bottom: 4, trailing: 12))
    }

    @ViewBuilder
    private var configChips: some View {
        let chips = ChatConfigCategory.chipOptions(model.state.config)
        let hasOthers = !ChatConfigCategory.otherOptions(model.state.config).isEmpty
        if !chips.isEmpty || hasOthers {
            // Wraps onto a second line rather than scrolling, so no chip is out of sight.
            ChipFlowLayout(horizontalSpacing: 8, verticalSpacing: 0) {
                ForEach(chips) { option in
                    chip(option)
                }
                if hasOthers {
                    ChatConfigChip(icon: "gearshape", text: "", tone: .muted, accessibilityLabel: "More settings") {
                        showSettings = true
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12)
            .disabled(!model.canAct)
        }
    }

    private func chip(_ option: ChatConfigOption) -> some View {
        let value = model.configValue(option)
        let current: String
        if case .string(let raw) = value {
            current = option.options.first { $0.value == raw }?.name ?? raw
        } else {
            current = option.name
        }
        var tone = PixelTone.muted
        if option.category == "mode", case .string(let raw) = value {
            tone = ChatConfigCategory.modeTone(raw)
        }
        return ChatConfigChip(
            icon: ChatConfigCategory.icon(option),
            text: current,
            tone: tone,
            accessibilityLabel: "\(option.name): \(current)"
        ) {
            pickingConfig = option
        }
    }

    // MARK: - Drawer edge

    /// The left edge opens the drawer, as on the terminal screen.
    private var edgePanStrip: some View {
        Color.clear
            .frame(width: 16)
            .frame(maxHeight: .infinity)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 10)
                    .onChanged { value in
                        if !drawerOpen { drawerDrag = value.translation.width }
                    }
                    .onEnded { value in
                        drawerDrag = nil
                        if SideDrawer<EmptyView>.shouldOpen(
                            translation: value.translation.width,
                            velocity: value.velocity.width
                        ) {
                            drawerOpen = true
                        }
                    }
            )
            .accessibilityHidden(true)
            .allowsHitTesting(!drawerOpen)
    }

    // MARK: - Actions

    private func hideKeyboard() {
        ChatKeyboard.hide()
    }

    private var focusViewBinding: Binding<Bool> {
        Binding(
            get: { focusView },
            set: { on in
                focusView = on
                container.preferencesStore.setChatFocusView(on)
            }
        )
    }

    private var stopPresented: Binding<Bool> {
        Binding(get: { pendingStop != nil }, set: { if !$0 { pendingStop = nil } })
    }

    /// Stopping the session on screen moves to the nearest open session, or
    /// the list when none is left, unless the user moved on meanwhile.
    private func stopSession(_ id: String, force: Bool) async {
        let stopped: SessionsPanelModel.StoppedSession
        do {
            guard let result = try await panel.stopSession(id, force: force) else { return }
            stopped = result
        } catch {
            model.noteReviewError()
            model.message = ToastMessage(text: "Failed to stop session: \(String(describing: error))", tone: .danger)
            return
        }
        guard id == sessionId else {
            model.message = ToastMessage(text: "Session stopped", tone: .ok)
            return
        }
        guard router.path.last == .sessionDetail(sessionId: id) else { return }
        model.markEndedByUser()
        guard let next = stopped.nextSessionId else {
            drawerOpen = false
            router.popTo(.sessionList)
            return
        }
        if drawerOpen {
            onSwitchAfterStop(next, id)
        } else {
            router.replaceLast(with: .sessionDetail(sessionId: next))
        }
    }

    private func checkSessionStillExists() {
        let repository = container.sessionRepository
        let isListed = repository.currentSessions.contains { $0.session_id == sessionId }
        guard model.noteSessionSnapshot(isListed: isListed, synced: repository.sessionsSynced) else { return }
        repository.notifySessionEnded(id: sessionId)
        router.popTo(.sessionList)
    }

    /// Read-only cold-start seed of the header (see the terminal screen).
    private func seedSessionRow() async {
        guard sessionRow == nil else { return }
        let list = try? await container.agentAPI.listSessions()
        guard sessionRow == nil else { return }
        sessionRow = list?.first { $0.session_id == sessionId }
        if let kind = sessionRow?.kind { voice.sessionKind = kind }
    }

    private func loadTemplates() async -> [TemplateDto] {
        guard let token = await container.userTokenProvider.userToken() else { return [] }
        return (try? await container.centralAPI.listTemplates(userToken: token)) ?? []
    }

    private func applyTemplate(_ template: TemplateDto) {
        model.insertTemplate(template)
        let centralAPI = container.centralAPI
        let tokens = container.userTokenProvider
        Task {
            guard let token = await tokens.userToken() else { return }
            _ = try? await centralAPI.touchTemplate(userToken: token, templateId: template.template_id)
        }
    }

    private func saveTemplate() {
        let name = templateName.trimmingCharacters(in: .whitespacesAndNewlines)
        let body = model.composerText
        guard !name.isEmpty, !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let centralAPI = container.centralAPI
        let tokens = container.userTokenProvider
        Task {
            guard let token = await tokens.userToken() else { return }
            _ = try? await centralAPI.createTemplate(userToken: token, name: name, body: body)
        }
    }

    private static func typeNames(_ types: [CustomAgentTypeDto]) -> [String: String] {
        Dictionary(types.map { ("custom_\($0.id)", $0.name) }, uniquingKeysWith: { _, last in last })
    }
}

@MainActor
enum ChatKeyboard {
    static func hide() {
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }
}

/// Title over the status line (Working · 12s, Waiting for you, …).
private struct ChatTitleView: View {
    let title: String
    let tone: PixelTone
    let model: ChatSessionModel

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(spacing: 1) {
            HStack(spacing: 6) {
                PixelStatusSquare(tone: tone)
                    .accessibilityHidden(true)
                Text(title)
                    .font(MobaideTypography.chrome(15, relativeTo: .headline))
                    .foregroundStyle(colors.accent)
                    .lineLimit(1)
            }
            if model.isRunning && model.isLive && model.state.requests.isEmpty && !model.state.cancelling {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    statusText(ChatStatusLine.text(for: model, now: context.date))
                }
            } else {
                statusText(ChatStatusLine.text(for: model, now: Date()))
            }
        }
        .accessibilityElement(children: .combine)
        .onChange(of: ChatStatusLine.text(for: model, now: nil).text) { _, status in
            AccessibilityNotification.Announcement(status).post()
        }
    }

    private func statusText(_ line: (text: String, tone: PixelTone?)) -> some View {
        Text(line.text)
            .font(.caption2)
            .foregroundStyle(line.tone.map(colors.color) ?? colors.textMuted)
            .lineLimit(1)
            .monospacedDigit()
    }
}

/// The status line under the title; the first matching condition wins.
@MainActor
enum ChatStatusLine {
    /// With no `now` the running line leaves out the elapsed time.
    static func text(for model: ChatSessionModel, now: Date?) -> (text: String, tone: PixelTone?) {
        if model.sessionClosed { return ("Session closed", nil) }
        if !model.isLive { return ("Reconnecting…", nil) }
        if !model.state.requests.isEmpty { return ("Waiting for you", .accent) }
        switch model.phase {
        case .starting: return ("Starting…", nil)
        case .loading: return ("Opening the session…", nil)
        default: break
        }
        if model.state.cancelling { return ("Stopping…", nil) }
        switch model.phase {
        case .running:
            guard let now, let started = model.state.turnStartedAt.flatMap(ChatDates.parse) else { return ("Working", nil) }
            return ("Working · \(elapsed(max(0, now.timeIntervalSince(started))))", nil)
        case .error: return ("Stopped", .danger)
        case .stopped: return ("Session closed", nil)
        default: return ("Ready", nil)
        }
    }

    static func elapsed(_ seconds: TimeInterval) -> String {
        let total = Int(seconds)
        let hours = total / 3600
        let minutes = (total % 3600) / 60
        let secs = total % 60
        if hours > 0 { return String(format: "%dh %02dm", hours, minutes) }
        if minutes > 0 { return String(format: "%dm %02ds", minutes, secs) }
        return "\(secs)s"
    }
}

enum ChatDates {
    static func parse(_ raw: String) -> Date? {
        if let date = try? Date(raw, strategy: .iso8601) { return date }
        return try? Date(raw, strategy: Date.ISO8601FormatStyle(includingFractionalSeconds: true))
    }

    /// "Just now", "5 min ago", "3 h ago", "Yesterday", "4 days ago", then a date.
    static func relative(_ date: Date, now: Date = Date(), calendar: Calendar = .current) -> String {
        let seconds = now.timeIntervalSince(date)
        if seconds < 60 { return "Just now" }
        if seconds < 3600 { return "\(Int(seconds / 60)) min ago" }
        if calendar.isDateInToday(date) { return "\(Int(seconds / 3600)) h ago" }
        if calendar.isDateInYesterday(date) { return "Yesterday" }
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: date), to: calendar.startOfDay(for: now)).day ?? 0
        if days < 7 { return "\(days) days ago" }
        let sameYear = calendar.component(.year, from: date) == calendar.component(.year, from: now)
        return date.formatted(sameYear ? .dateTime.month(.abbreviated).day() : .dateTime.year().month(.abbreviated).day())
    }
}

/// The terminal's `.terminal-overlay` chip.
struct ChatOverlayChip: View {
    let text: String

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Text(text)
            .chromeText()
            .foregroundStyle(colors.text)
            .padding(EdgeInsets(top: 6, leading: 14, bottom: 6, trailing: 14))
            .background(colors.surface.opacity(0.92))
            .pixelRing(colors.rule)
            .padding(.top, 8)
            .allowsHitTesting(false)
            .accessibilityAddTraits(.updatesFrequently)
    }
}

/// Agent, folder and ACP session ID, each copyable.
private struct ChatSessionInfoSheet: View {
    let agent: ChatAgentInfo?
    let cwd: String?
    let acpSessionId: String?

    @Environment(\.dismiss) private var dismiss

    private var agentText: String? {
        guard let agent else { return nil }
        let name = agent.displayName ?? ""
        let text = [name, agent.version ?? ""].filter { !$0.isEmpty }.joined(separator: " ")
        return text.isEmpty ? nil : text
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 12) {
                    if let agentText {
                        CopyableCodeBlock(label: "Agent", code: agentText)
                    }
                    if let cwd {
                        CopyableCodeBlock(label: "Folder", code: cwd)
                    }
                    if let acpSessionId {
                        CopyableCodeBlock(label: "ACP session ID", code: acpSessionId)
                    }
                    if agentText == nil, cwd == nil, acpSessionId == nil {
                        PixelEmptyState("No details yet", systemImage: "info.circle")
                    }
                }
                .padding(16)
            }
            .pixelScreenBackground()
            .navigationTitle("Session info")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .pixelSheet()
        .presentationDetents([.medium, .large])
    }
}
