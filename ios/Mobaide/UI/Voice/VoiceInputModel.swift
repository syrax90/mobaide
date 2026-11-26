import Observation
import SwiftUI
import UIKit

/// A dictation is cancelled when the agent stops being connected (a switch),
/// when its transcriber can no longer take it (on-computer voice leaves
/// `ready`, or the engine changes) and when the screen goes away; it is
/// stopped and transcribed when the app leaves the foreground.
@MainActor
@Observable
final class VoiceInputModel {
    static let defaultAgentName = "your computer"
    private static let queuedPollSeconds: UInt64 = 4
    private static let cloudUnavailablePollSeconds: UInt64 = 30
    private static let unreachablePollSeconds: UInt64 = 5

    private(set) var capabilityKnown = false
    /// The agent did not answer the capability probe: whether it has voice of
    /// its own is not known.
    private(set) var capabilityLoadFailed = false
    /// `load()` has finished: what the agent's own voice can do is known.
    private(set) var localProbed = false
    /// The agent has voice of its own, and its status could not be read.
    private(set) var statusLoadFailed = false
    private(set) var capability: VoiceCapability?
    private(set) var micHidden = false
    private(set) var agentName = VoiceInputModel.defaultAgentName
    private(set) var controller: DictationController?
    private(set) var permissionFailure: DictationState?
    private(set) var actionBusy = false
    private(set) var actionError: String?
    /// The agent did not store the last cleanup change.
    private(set) var cleanupSaveError: String?
    /// "Got it" was pressed on the notice shown before the first cleanup opt-in.
    private(set) var cleanupNoticeSeen = false
    /// An engine choice is on its way to the server.
    private(set) var engineSaving = false
    /// The server did not store the last engine choice.
    private(set) var engineSaveFailed = false
    var setupVisible = false
    /// The terminal's session; nil on Settings, where nothing is cleaned up.
    var sessionKind: SessionKind?

    /// Puts a transcript into the composer; never sends it.
    @ObservationIgnored var onTranscript: ((String) -> Void)?
    /// Says why a transcript was inserted as heard, for a toast.
    @ObservationIgnored var onNotice: ((String) -> Void)?

    @ObservationIgnored private let repository: VoiceRepository
    @ObservationIgnored private let cloudRepository: CloudVoiceRepository
    @ObservationIgnored private let preferences: PreferencesStore
    @ObservationIgnored private let agentRepository: AgentRepository
    @ObservationIgnored private let sessionManager: SessionManager
    @ObservationIgnored private let agentKey: String?
    @ObservationIgnored private let agentId: String?
    @ObservationIgnored private let sessionId: String?
    @ObservationIgnored private var wasRecording = false
    /// Who the current controller uploads to.
    private var controllerSource: DictationSource?
    @ObservationIgnored private var engineSaves = 0
    /// The probe of the agent's own voice that is in flight.
    @ObservationIgnored private var localProbe: Task<Void, Never>?

    init(container: AppContainer, sessionId: String?) {
        repository = container.voiceRepository
        cloudRepository = container.cloudVoiceRepository
        preferences = container.preferencesStore
        agentRepository = container.agentRepository
        sessionManager = container.sessionManager
        agentKey = container.tokenStore.getAgentScopeKey()
        agentId = container.tokenStore.getAgentId()
        self.sessionId = sessionId
        micHidden = agentKey.map { preferences.isVoiceMicHidden(agentKey: $0) } ?? false
        cleanupNoticeSeen = preferences.isVoiceCleanupNoticeSeen()
        if let agentId, let agent = agentRepository.currentAgents.first(where: { $0.agent_id == agentId }) {
            agentName = agent.name
        }
    }

    var status: VoiceStatusResponse? { repository.status }

    /// Stored on the agent, so every client of it shares the choice.
    var cleanUp: Bool { status?.cleanup_enabled == true }

    /// The account's cloud voice input. Nil without an agent id, where no
    /// engine choice can be stored.
    var cloud: CloudVoiceStatus? { agentId == nil ? nil : cloudRepository.status }

    var cloudOffered: Bool { cloud?.isOffered == true }

    var cloudChecking: Bool { cloudRepository.refreshing }

    /// This agent dictates through cloud voice input, which does not answer.
    var cloudDown: Bool { route == .cloud && cloud?.state == CloudVoiceStatus.stateUnavailable }

    var route: VoiceRoute {
        VoiceRoute.resolve(cloud: cloud, agentId: agentId, localStatus: status)
    }

    /// The agent has voice input of its own (whatever its install state).
    var localSupported: Bool { capability != nil }

    /// The agent did not answer when asked about its own voice.
    var localUnreachable: Bool {
        capabilityLoadFailed || (capability != nil && status == nil && statusLoadFailed)
    }

    /// Why "On this computer" cannot be chosen, or nil when it can.
    var localBlocker: String? {
        if !capabilityKnown { return CloudVoiceCopy.checking(agent: agentName) }
        if capabilityLoadFailed { return CloudVoiceCopy.unreachable(agent: agentName) }
        return CloudVoiceCopy.localBlocker(
            agent: agentName,
            localSupported: localSupported,
            status: status,
            loadFailed: statusLoadFailed
        )
    }

    var micMode: VoiceMicMode {
        // No choice yet and the agent not probed yet: its own voice may turn
        // out to be enabled, which needs no chooser.
        if route == .choose, !localProbed { return .hidden }
        return VoiceStatusView.composerMicMode(
            route: route,
            cloud: cloud,
            localMode: VoiceStatusView.micMode(capability: capability, status: status, hiddenByUser: micHidden),
            hiddenByUser: micHidden
        )
    }

    /// Who would transcribe a dictation started now; nil while nothing can record.
    var dictationSource: DictationSource? {
        switch route {
        case .cloud:
            guard let cloud, cloud.state == CloudVoiceStatus.stateReady else { return nil }
            return DictationSource(
                engine: .cloud,
                sampleRate: cloud.limits.sample_rate,
                maxSeconds: cloud.limits.max_seconds
            )
        case .local:
            guard let capability, status?.state == VoiceStatusResponse.stateReady else { return nil }
            return DictationSource(engine: .local, sampleRate: capability.sampleRate, maxSeconds: capability.maxSeconds)
        case .choose:
            return nil
        }
    }

    /// The screens watch this and call `dictationTargetChanged()`.
    var dictationTarget: DictationTarget { DictationTarget(route: route, source: dictationSource) }

    /// Who transcribes, for copy like "Transcribing on …": the dictation in
    /// hand decides, then the route.
    var transcriberName: String {
        let engine = controllerSource?.engine ?? (route == .cloud ? .cloud : .local)
        return engine == .cloud ? CloudVoiceCopy.cloudName : agentName
    }

    var dictation: DictationState { permissionFailure ?? controller?.state ?? .idle }

    /// The agent can clean up transcripts (`capabilities.voice.cleanup`).
    var cleanupSupported: Bool { capability?.cleanup == true }

    /// ...for this session's kind (`capabilities.voice.cleanup_kinds`): the
    /// agent runs that kind's CLI.
    var cleanupOffered: Bool { capability?.cleansUp(for: sessionKind) == true }

    /// Probe the agent once per screen visit, and read the cloud voice status
    /// again when the cached one is a minute old. Run from the `.task` of
    /// every voice surface: the session screens and Settings → Voice input.
    func load() async {
        if agentId != nil {
            Task { await cloudRepository.refreshIfStale() }
        }
        await probeLocal()
    }

    /// Ask the agent whether it has voice of its own, and for its status.
    /// One at a time: a caller during a probe waits for that probe. It runs
    /// in its own task, so a view that goes away does not cut it short.
    private func probeLocal() async {
        if let localProbe { return await localProbe.value }
        let probe = Task { @MainActor in
            defer { self.localProbe = nil }
            do {
                self.capability = try await self.repository.capability()
                self.capabilityLoadFailed = false
            } catch is AgentURLNotConfiguredError {
                // No agent is selected, so there is nobody to reach: that is
                // "no voice", not an agent that did not answer.
                self.capability = nil
                self.capabilityLoadFailed = false
            } catch {
                self.capability = nil
                self.capabilityLoadFailed = true
            }
            self.capabilityKnown = true
            if self.capability != nil {
                do {
                    try await self.repository.refresh()
                    self.statusLoadFailed = false
                } catch {
                    self.statusLoadFailed = true
                }
            }
            self.localProbed = true
        }
        localProbe = probe
        await probe.value
    }

    /// Nothing says that an agent which did not answer is back, so while its
    /// own voice could not be read and a voice surface is on screen, it is
    /// asked again by itself (the caller's `.task(id:)` scopes it).
    func pollWhileUnreachable() async {
        while localUnreachable, !Task.isCancelled {
            try? await Task.sleep(nanoseconds: Self.unreachablePollSeconds * 1_000_000_000)
            guard !Task.isCancelled else { return }
            await probeLocal()
        }
    }

    /// While another process holds the install (`queued`), agents older than
    /// the agent's lock watcher publish nothing, so the status is polled while
    /// a voice surface is on screen (the caller's `.task(id:)` scopes it).
    func pollWhileQueued() async {
        while status?.state == VoiceStatusResponse.stateQueued, !Task.isCancelled {
            try? await Task.sleep(nanoseconds: Self.queuedPollSeconds * 1_000_000_000)
            guard !Task.isCancelled else { return }
            _ = try? await repository.refresh()
        }
    }

    /// While cloud voice input is down and a voice surface is on screen, ask
    /// again by itself: the server does not announce that it is back (the
    /// caller's `.task(id:)` scopes it).
    func pollWhileCloudUnavailable() async {
        while cloudDown, !Task.isCancelled {
            try? await Task.sleep(nanoseconds: Self.cloudUnavailablePollSeconds * 1_000_000_000)
            guard !Task.isCancelled else { return }
            try? await cloudRepository.refreshCloudVoice()
        }
    }

    /// Cancels a dictation when the agent stops being connected.
    func watchConnection() async {
        for await state in sessionManager.authStates() where state != .fullyConnected {
            disposeController()
        }
    }

    /// A cloud dictation is kept until it is over, whatever the
    /// status does meanwhile: "unavailable" arrives exactly when a recording
    /// failed, and dropping the controller then would drop the audio Retry
    /// needs.
    func dictationTargetChanged() {
        guard let controllerSource, controllerSource != dictationSource else { return }
        if controllerSource.engine == .cloud, let controller, controller.state != .idle { return }
        disposeController()
    }

    func micTapped() {
        if let controller, controller.state.isActive {
            controller.toggle()
            return
        }
        if micMode == .ready {
            startDictation()
            return
        }
        // The cached status may be a minute old: ask again while the sheet opens.
        if micMode == .unavailable { recheckCloud() }
        showSetup()
    }

    func startDictation() {
        permissionFailure = nil
        guard let source = dictationSource else {
            // "Again" after the transcriber stopped being ready: say why.
            disposeController()
            if micMode != .hidden { showSetup() }
            return
        }
        if controllerSource != source { disposeController() }
        guard let controller = controller ?? makeController(source) else { return }
        controller.toggle()
    }

    func stopDictation() { controller?.stop() }

    func cancelDictation() {
        permissionFailure = nil
        controller?.cancel()
    }

    func retryDictation() { controller?.retry() }

    func dismissDictation() {
        permissionFailure = nil
        controller?.dismiss()
    }

    /// The app left the foreground: keep what was said and transcribe it.
    func interrupt() {
        controller?.stop(reason: .background)
    }

    /// Leaving the terminal screen.
    func leave() {
        controller?.cancel()
    }

    func showSetup() {
        engineSaveFailed = false
        setupVisible = true
        guard capability != nil else { return }
        Task {
            do {
                try await repository.refresh()
                statusLoadFailed = false
            } catch {
                statusLoadFailed = status == nil
            }
        }
    }

    func hideSetup() {
        setupVisible = false
        actionError = nil
        cleanupSaveError = nil
        engineSaveFailed = false
    }

    func recheckCloud() {
        Task { await recheckCloudNow() }
    }

    /// Returns once the fresh status is in (or could not be read).
    func recheckCloudNow() async {
        try? await cloudRepository.refreshCloudVoice()
    }

    /// The chooser's Continue. Cloud closes the sheet: the next mic tap
    /// records. The computer starts its download, whose costs the card showed,
    /// and the sheet turns into the progress.
    func confirmEngine(_ engine: VoiceEngine) {
        saveEngine(engine) { [weak self] in
            guard let self else { return }
            if engine == .cloud {
                self.hideSetup()
            } else if self.status?.state == VoiceStatusResponse.stateNotInstalled {
                self.install()
            }
        }
    }

    /// Settings → Speech recognition.
    func pickEngine(_ engine: VoiceEngine) {
        let current: VoiceEngine? = route == .cloud ? .cloud : (route == .local ? .local : nil)
        if current != engine { saveEngine(engine) }
    }

    /// "Use Mobaide Cloud instead" on the enable sheet.
    func useCloud() {
        saveEngine(.cloud) { [weak self] in self?.hideSetup() }
    }

    /// "Use this computer instead" while cloud voice input cannot record.
    func useLocal() {
        saveEngine(.local)
    }

    /// Stored on the central server for every device. The route changes at
    /// once and goes back when the server refuses.
    private func saveEngine(_ engine: VoiceEngine, then: (@MainActor () -> Void)? = nil) {
        guard let agentId else { return }
        engineSaveFailed = false
        engineSaves += 1
        engineSaving = true
        Task {
            do {
                try await cloudRepository.setEngine(engine, agentId: agentId)
                then?()
            } catch {
                engineSaveFailed = true
                // The sheet that would show it is closed: say it in a toast.
                if !setupVisible { onNotice?(CloudVoiceCopy.choiceSaveFailed) }
            }
            engineSaves -= 1
            engineSaving = engineSaves > 0
        }
    }

    func install() { runAction { try await $0.install() } }
    func cancelInstall() { runAction { try await $0.cancelInstall() } }
    func remove() { runAction { try await $0.remove() } }
    func reinstall() { runAction { try await $0.reinstall() } }

    func setMicHidden(_ hidden: Bool) {
        guard let agentKey else { return }
        preferences.setVoiceMicHidden(agentKey: agentKey, hidden: hidden)
        micHidden = hidden
        if hidden { setupVisible = false }
    }

    /// Turning cleanup on also records the notice as seen: the UI shows the
    /// notice first, and calls this only after "Got it".
    func setCleanUp(_ on: Bool) {
        if on {
            preferences.setVoiceCleanupNoticeSeen()
            cleanupNoticeSeen = true
        }
        cleanupSaveError = nil
        Task {
            do {
                try await repository.setCleanup(on)
            } catch {
                cleanupSaveError = VoiceCleanupCopy.saveFailed(agent: agentName)
            }
        }
    }

    private func runAction(_ action: @escaping (VoiceRepository) async throws -> VoiceStatusResponse) {
        guard !actionBusy else { return }
        actionBusy = true
        actionError = nil
        Task {
            do {
                _ = try await action(repository)
            } catch let error as VoiceApiError {
                actionError = error.network ? "Couldn't reach \(agentName)." : VoiceErrorCopy.action(error.code)
            } catch {
                actionError = VoiceErrorCopy.action(nil)
            }
            actionBusy = false
        }
    }

    private func makeController(_ source: DictationSource) -> DictationController? {
        let created = DictationController(
            api: source.engine == .cloud
                ? cloudRepository.dictationApi()
                : repository.dictationApi(sessionId: sessionId),
            makeRecorder: { AudioEngineRecorder() },
            sampleRate: source.sampleRate,
            maxSeconds: source.maxSeconds,
            cleaner: { [weak self] in self?.activeCleaner() }
        )
        created.onTransition = { [weak self, weak created] state in
            self?.handleTransition(state, controller: created)
        }
        controller = created
        controllerSource = source
        return created
    }

    /// The speech service already returns clean text, so cloud transcripts
    /// skip the cleanup.
    private func activeCleaner() -> TranscriptCleaner? {
        guard controllerSource?.engine == .local else { return nil }
        guard cleanupOffered, cleanUp, let kind = sessionKind?.wireName else { return nil }
        let repository = repository
        return { text in try await repository.cleanup(text: text, kind: kind) }
    }

    private func handleTransition(_ state: DictationState, controller: DictationController?) {
        let recording: Bool
        if case .recording = state { recording = true } else { recording = false }
        if recording != wasRecording {
            UIImpactFeedbackGenerator(style: recording ? .medium : .light).impactOccurred()
            AccessibilityNotification.Announcement(recording ? "Recording" : "Recording stopped").post()
            wasRecording = recording
        }
        // The agent says voice is not ready while the cached status says it
        // is: re-read it so the mic shows the real state.
        if case .failed(let code, _, _, _) = state, code == VoiceCodes.notReady {
            Task { _ = try? await repository.refresh() }
        }
        // The same for cloud voice input, and after each cloud transcript for
        // today's use.
        if case .failed(let code, _, _, _) = state, VoiceCodes.cloudStatusCodes.contains(code) {
            recheckCloud()
        }
        if state.isResult, controllerSource?.engine == .cloud {
            recheckCloud()
        }
        // A cloud dictation that was held past a status change is over.
        if state == .idle {
            Task { self.dictationTargetChanged() }
        }
        if case .result(let text, let cleanupError) = state {
            onTranscript?(text)
            AccessibilityNotification.Announcement("Transcript inserted").post()
            if let cleanupError {
                onNotice?(
                    "Couldn't clean up the text, so it's inserted as heard. "
                        + VoiceErrorCopy.cleanup(cleanupError.code, agent: agentName, kind: sessionKind?.wireName)
                )
            }
            Task { controller?.dismiss() }
        }
    }

    private func disposeController() {
        controller?.dispose()
        controller = nil
        controllerSource = nil
    }
}
