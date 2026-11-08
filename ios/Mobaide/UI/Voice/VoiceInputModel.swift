import Observation
import SwiftUI
import UIKit

/// A dictation is cancelled when the agent stops being connected (a switch),
/// when voice leaves `ready` and when the screen goes away; it is stopped and
/// transcribed when the app leaves the foreground.
@MainActor
@Observable
final class VoiceInputModel {
    static let defaultAgentName = "your computer"
    private static let queuedPollSeconds: UInt64 = 4

    private(set) var capabilityKnown = false
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
    var setupVisible = false
    /// The terminal's session; nil on Settings, where nothing is cleaned up.
    var sessionKind: SessionKind?

    /// Puts a transcript into the composer; never sends it.
    @ObservationIgnored var onTranscript: ((String) -> Void)?
    /// Says why a transcript was inserted as heard, for a toast.
    @ObservationIgnored var onNotice: ((String) -> Void)?

    @ObservationIgnored private let repository: VoiceRepository
    @ObservationIgnored private let preferences: PreferencesStore
    @ObservationIgnored private let agentRepository: AgentRepository
    @ObservationIgnored private let sessionManager: SessionManager
    @ObservationIgnored private let agentKey: String?
    @ObservationIgnored private let agentId: String?
    @ObservationIgnored private let sessionId: String?
    @ObservationIgnored private var wasRecording = false

    init(container: AppContainer, sessionId: String?) {
        repository = container.voiceRepository
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

    var micMode: VoiceMicMode {
        VoiceStatusView.micMode(capability: capability, status: status, hiddenByUser: micHidden)
    }

    var dictation: DictationState { permissionFailure ?? controller?.state ?? .idle }

    /// The agent can clean up transcripts (`capabilities.voice.cleanup`).
    var cleanupSupported: Bool { capability?.cleanup == true }

    /// ...for this session's kind (`capabilities.voice.cleanup_kinds`): the
    /// agent runs that kind's CLI.
    var cleanupOffered: Bool { capability?.cleansUp(for: sessionKind) == true }

    /// Probe the agent once per screen visit; run from the view's `.task`.
    func load() async {
        capability = (try? await repository.capability()) ?? nil
        capabilityKnown = true
        if capability != nil { _ = try? await repository.refresh() }
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

    /// Cancels a dictation when the agent stops being connected.
    func watchConnection() async {
        for await state in sessionManager.authStates() where state != .fullyConnected {
            disposeController()
        }
    }

    func statusChanged() {
        if status?.state != VoiceStatusResponse.stateReady { disposeController() }
    }

    func micTapped() {
        if micMode == .ready {
            startDictation()
        } else {
            showSetup()
        }
    }

    func startDictation() {
        permissionFailure = nil
        guard let controller = controller ?? makeController() else { return }
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
        setupVisible = true
        Task { _ = try? await repository.refresh() }
    }

    func hideSetup() {
        setupVisible = false
        actionError = nil
        cleanupSaveError = nil
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

    private func makeController() -> DictationController? {
        guard let capability else { return nil }
        let created = DictationController(
            api: repository.dictationApi(sessionId: sessionId),
            makeRecorder: { AudioEngineRecorder() },
            sampleRate: capability.sampleRate,
            maxSeconds: capability.maxSeconds,
            cleaner: { [weak self] in self?.activeCleaner() }
        )
        created.onTransition = { [weak self, weak created] state in
            self?.handleTransition(state, controller: created)
        }
        controller = created
        return created
    }

    private func activeCleaner() -> TranscriptCleaner? {
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
    }
}
