import Foundation
import Observation

/// The status is reconciled by `voice.changed`, a full snapshot the agent also
/// republishes on every events-channel connect, so an agent switch needs no
/// REST refresh; leaving the connected state clears it.
@MainActor
@Observable
final class VoiceRepository {
    private(set) var status: VoiceStatusResponse?

    @ObservationIgnored private let agentAPI: AgentAPI
    @ObservationIgnored private let agentEventBus: AgentEventBus
    @ObservationIgnored private let sessionManager: SessionManager
    @ObservationIgnored private var subscriptions: [Task<Void, Never>] = []
    /// Bumped by every `voice.changed`, so an older GET answer never overwrites one.
    @ObservationIgnored private var events = 0

    init(agentAPI: AgentAPI, agentEventBus: AgentEventBus, sessionManager: SessionManager) {
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        self.sessionManager = sessionManager
    }

    /// Idempotent. Subscribe before `AgentEventBus.start()` so the snapshot the
    /// bus re-pushes on connect is not missed (the events stream has no replay).
    func start() {
        guard subscriptions.isEmpty else { return }
        let events = agentEventBus.events()
        subscriptions.append(Task { [weak self] in
            for await event in events {
                if case .voiceChanged(let status) = event {
                    self?.events += 1
                    self?.status = status
                }
            }
        })
        let states = sessionManager.authStates()
        subscriptions.append(Task { [weak self] in
            for await state in states where state != .fullyConnected {
                self?.status = nil
            }
        })
    }

    /// `capabilities.voice` of the connected agent; nil when it has no voice.
    func capability() async throws -> VoiceCapability? {
        try await agentAPI.getAgentVersion().voiceCapability
    }

    /// A snapshot that arrived while this request was in flight is newer and wins.
    @discardableResult
    func refresh() async throws -> VoiceStatusResponse {
        let before = events
        let answer: VoiceStatusResponse
        do {
            answer = try await agentAPI.getVoiceStatus()
        } catch {
            throw VoiceApiError.map(error)
        }
        if events == before { status = answer }
        return answer
    }

    @discardableResult
    func install() async throws -> VoiceStatusResponse {
        try await write { try await $0.installVoice() }
    }

    /// Stop an install; while repairing, only the runtime download stops.
    @discardableResult
    func cancelInstall() async throws -> VoiceStatusResponse {
        try await write { try await $0.cancelVoiceInstall() }
    }

    /// Turn voice off and free the disk.
    @discardableResult
    func remove() async throws -> VoiceStatusResponse {
        try await write { try await $0.removeVoice() }
    }

    @discardableResult
    func reinstall() async throws -> VoiceStatusResponse {
        try await remove()
        return try await install()
    }

    /// Stores the cleanup choice on the agent for every client. The status
    /// changes at once and goes back when the agent refuses.
    @discardableResult
    func setCleanup(_ on: Bool) async throws -> VoiceStatusResponse {
        let previous = status
        let optimistic = previous?.withCleanup(on)
        if let optimistic { status = optimistic }
        do {
            return try await write { try await $0.setVoiceCleanup(enabled: on) }
        } catch {
            if status == optimistic { status = previous }
            throw error
        }
    }

    /// The session's AI agent cleans up `text` on the connected agent's
    /// computer (within 20 s).
    nonisolated func cleanup(text: String) async throws -> String {
        do {
            return try await agentAPI.cleanupVoiceTranscript(text: text).text
        } catch {
            throw VoiceApiError.map(error)
        }
    }

    /// The dictation routes of the connected agent, for the uploader.
    nonisolated func dictationApi(sessionId: String?) -> any VoiceApi {
        AgentVoiceApi(agentAPI: agentAPI, sessionId: sessionId)
    }

    private func write(_ call: (AgentAPI) async throws -> VoiceStatusResponse) async throws -> VoiceStatusResponse {
        do {
            let next = try await call(agentAPI)
            status = next
            return next
        } catch {
            throw VoiceApiError.map(error)
        }
    }
}

private struct AgentVoiceApi: VoiceApi {
    let agentAPI: AgentAPI
    let sessionId: String?

    func create() async throws -> VoiceDictationOpened {
        do {
            let body = try await agentAPI.createVoiceDictation(sessionId: sessionId)
            return VoiceDictationOpened(
                dictationId: body.dictation_id,
                sampleRate: body.sample_rate,
                maxSeconds: body.max_seconds,
                maxChunkBytes: body.max_chunk_bytes
            )
        } catch {
            throw VoiceApiError.map(error)
        }
    }

    func audio(dictationId: String, seq: Int, pcm16B64: String) async throws {
        do {
            try await agentAPI.appendVoiceAudio(dictationId: dictationId, seq: seq, pcm16B64: pcm16B64)
        } catch {
            throw VoiceApiError.map(error)
        }
    }

    func finish(dictationId: String, lastSeq: Int) async throws -> VoiceFinishResult {
        do {
            let body = try await agentAPI.finishVoiceDictation(dictationId: dictationId, lastSeq: lastSeq)
            return VoiceFinishResult(state: body.state, text: body.text)
        } catch {
            throw VoiceApiError.map(error)
        }
    }

    func discard(dictationId: String) {
        let agentAPI = agentAPI
        Task.detached {
            try? await agentAPI.deleteVoiceDictation(dictationId: dictationId)
        }
    }
}
