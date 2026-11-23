import Foundation
import Observation

/// `status` is nil until the first answer, and when the server has no such
/// route or nobody is signed in: all of them read as "not offered". An engine
/// change shows at once and goes back when the server refuses.
@MainActor
@Observable
final class CloudVoiceRepository: CloudVoiceSyncApplying {
    private(set) var status: CloudVoiceStatus?
    /// A fresh status is on its way.
    private(set) var refreshing = false

    @ObservationIgnored private let centralAPI: CentralAPI
    @ObservationIgnored private let userIdentityStore: UserIdentityStore
    @ObservationIgnored private let now: @Sendable () -> Date
    /// The last status the server sent; `status` is this plus the unsaved choices.
    @ObservationIgnored private var confirmed: CloudVoiceStatus?
    @ObservationIgnored private var confirmedAt: Date?
    /// Agent id → the choice being saved. `token` tells two saves of the same
    /// engine apart, so the older one does not release the newer one's.
    @ObservationIgnored private var pending: [String: (engine: VoiceEngine, token: Int)] = [:]
    @ObservationIgnored private var nextToken = 0
    /// The last save in the chain; each new save waits for it, so saves reach
    /// the server in the order the user made them.
    @ObservationIgnored private var saveTail: Task<Void, Never>?
    /// Bumped by every saved choice and every clear, so an older GET answer
    /// never overwrites one.
    @ObservationIgnored private var writes = 0
    @ObservationIgnored private var refreshTask: Task<Void, any Error>?
    /// A refresh was asked for while one was in flight, whose answer may
    /// predate the reason for asking.
    @ObservationIgnored private var refreshQueued = false

    private static let staleSeconds: TimeInterval = 60

    init(
        centralAPI: CentralAPI,
        userIdentityStore: UserIdentityStore,
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.centralAPI = centralAPI
        self.userIdentityStore = userIdentityStore
        self.now = now
    }

    // MARK: - CloudVoiceSyncApplying

    /// One request at a time: a call during a refresh waits for it and for
    /// one more after it.
    func refreshCloudVoice() async throws {
        if let refreshTask {
            refreshQueued = true
            return try await refreshTask.value
        }
        let task = Task { @MainActor in
            defer {
                self.refreshTask = nil
                self.refreshQueued = false
                self.refreshing = false
            }
            repeat {
                self.refreshQueued = false
                try await self.load()
            } while self.refreshQueued
        }
        refreshTask = task
        refreshing = true
        try await task.value
    }

    /// The payload names the choices, which show at once; the status around
    /// them is read again, off the caller so the events stream is not held up.
    func applyEnginesChanged(_ payload: CloudVoiceChangedPayload) async {
        if let confirmed {
            self.confirmed = CloudVoiceStatus(
                state: confirmed.state,
                limits: confirmed.limits,
                usage: confirmed.usage,
                engines: payload.engines
            )
            publish()
        }
        Task { try? await self.refreshCloudVoice() }
    }

    func clear() async {
        writes += 1
        confirmed = nil
        confirmedAt = nil
        pending = [:]
        publish()
    }

    // MARK: - Reads and writes

    /// The server does not announce when the speech service goes down or
    /// comes back, so a screen that shows the status asks again once the
    /// cached one is a minute old.
    func refreshIfStale() async {
        if let confirmedAt, now().timeIntervalSince(confirmedAt) < Self.staleSeconds { return }
        try? await refreshCloudVoice()
    }

    /// Choose where one agent's dictations are transcribed, for every device
    /// of the account. The save runs in its own task, so it finishes even if
    /// the screen closes first. Throws a `VoiceApiError`.
    func setEngine(_ engine: VoiceEngine, agentId: String) async throws {
        let requestUserId = userIdentityStore.getUserId()
        nextToken += 1
        let token = nextToken
        pending[agentId] = (engine, token)
        publish()
        let previous = saveTail
        let save = Task { @MainActor () -> (any Error)? in
            await previous?.value
            defer { self.release(agentId: agentId, token: token) }
            // A save queued before a sign-out must not run with the next
            // account's token.
            guard self.userIdentityStore.getUserId() == requestUserId else { return nil }
            do {
                let saved = try await self.centralAPI.putCloudVoiceEngine(
                    userToken: try self.userToken(),
                    agentId: agentId,
                    engine: engine
                )
                if self.userIdentityStore.getUserId() == requestUserId {
                    self.writes += 1
                    self.confirm(saved)
                }
                return nil
            } catch {
                return VoiceApiError.map(error)
            }
        }
        saveTail = Task { _ = await save.value }
        if let error = await save.value { throw error }
    }

    /// The cloud dictation routes, for the uploader. They mirror the agent's
    /// body for body and refuse with the same envelope.
    nonisolated func dictationApi() -> any VoiceApi {
        CloudVoiceApi(centralAPI: centralAPI, userIdentityStore: userIdentityStore)
    }

    private func load() async throws {
        // Account-generation guard, as in `CustomAgentTypesRepository`: an
        // answer for one account must not land after it signed out.
        let requestUserId = userIdentityStore.getUserId()
        guard let token = userIdentityStore.getUserAccessToken() else {
            confirm(nil)
            return
        }
        let before = writes
        let answer: CloudVoiceStatus?
        do {
            answer = try await centralAPI.getCloudVoice(userToken: token)
        } catch let error as APIError where error.code == 404 {
            // A server from before cloud voice input has no such route.
            answer = nil
        }
        guard userIdentityStore.getUserId() == requestUserId, writes == before else { return }
        confirm(answer)
    }

    private func confirm(_ next: CloudVoiceStatus?) {
        confirmed = next
        confirmedAt = now()
        publish()
    }

    private func release(agentId: String, token: Int) {
        guard pending[agentId]?.token == token else { return }
        pending[agentId] = nil
        publish()
    }

    private func publish() {
        var next = confirmed
        for (agentId, choice) in pending {
            next = next?.withEngine(choice.engine, agentId: agentId)
        }
        if next != status { status = next }
    }

    private func userToken() throws -> String {
        guard let token = userIdentityStore.getUserAccessToken() else {
            throw APIError(code: -1, errorBody: "Not signed in")
        }
        return token
    }
}

private struct CloudVoiceApi: VoiceApi {
    let centralAPI: CentralAPI
    let userIdentityStore: UserIdentityStore

    func create() async throws -> VoiceDictationOpened {
        do {
            let body = try await centralAPI.createCloudVoiceDictation(userToken: try userToken())
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
            try await centralAPI.appendCloudVoiceAudio(
                userToken: try userToken(),
                dictationId: dictationId,
                seq: seq,
                pcm16B64: pcm16B64
            )
        } catch {
            throw VoiceApiError.map(error)
        }
    }

    func finish(dictationId: String, lastSeq: Int) async throws -> VoiceFinishResult {
        do {
            let body = try await centralAPI.finishCloudVoiceDictation(
                userToken: try userToken(),
                dictationId: dictationId,
                lastSeq: lastSeq
            )
            return VoiceFinishResult(state: body.state, text: body.text)
        } catch {
            throw VoiceApiError.map(error)
        }
    }

    func discard(dictationId: String) {
        guard let token = userIdentityStore.getUserAccessToken() else { return }
        let centralAPI = centralAPI
        Task.detached {
            try? await centralAPI.deleteCloudVoiceDictation(userToken: token, dictationId: dictationId)
        }
    }

    private func userToken() throws -> String {
        guard let token = userIdentityStore.getUserAccessToken() else {
            throw VoiceApiError(status: nil, code: nil, message: "Not signed in")
        }
        return token
    }
}
