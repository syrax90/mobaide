import Foundation

/// A failed upload; `retryable` means a Retry from the buffered audio can work.
struct UploadFailure: Equatable, Sendable {
    let code: String
    let retryable: Bool
    let message: String?
}

protocol VoiceClock: Sendable {
    func now() -> TimeInterval
    func sleep(seconds: Double) async throws
    func random() -> Double
}

struct SystemVoiceClock: VoiceClock {
    func now() -> TimeInterval { Date().timeIntervalSince1970 }
    func sleep(seconds: Double) async throws { try await Task.sleep(nanoseconds: UInt64(max(0, seconds) * 1e9)) }
    func random() -> Double { Double.random(in: 0..<1) }
}

/// A single pump task is the only code that issues requests, so exactly one
/// audio request is in flight per dictation and `finish` is only ever sent
/// after the last chunk was acknowledged. `onAudio`, `close` and `cancel` only
/// set state and wake the pump.
@MainActor
final class DictationUploader {
    struct Callbacks {
        var lagging: (Bool) -> Void
        /// The agent's limit was reached: stop recording and transcribe.
        var stopCapture: () -> Void
        var done: (String) -> Void
        var failed: (UploadFailure) -> Void
    }

    private struct Fatal: Error {
        let failure: UploadFailure
    }

    private struct Classified {
        let transient: Bool
        let status: Int?
        let code: String?
        let message: String?
        let expectedSeq: Int?

        static let timeout = Classified(transient: true, status: nil, code: nil, message: "timeout", expectedSeq: nil)
    }

    private enum FinishOutcome {
        case text(String)
        case resync
    }

    /// Above the relay's 30 s wait, so an id the agent did issue is never thrown away.
    private static let createTimeout = 35.0
    private static let audioTimeout = 15.0
    /// The agent waits up to 20 s and the relay 30 s before answering `finish`.
    private static let finishTimeout = 35.0
    /// After the user stops, how long a stalled upload may make no progress.
    private static let stallLimit = 30.0
    /// The agent keeps a finished result for 2 minutes.
    private static let finishDeadline = 120.0
    private static let minFinishPoll = 1.0
    private static let maxRestarts = 2
    private static let maxResyncs = 3
    private static let backoffSeconds = [0.5, 1.0, 2.0, 4.0]

    private let api: any VoiceApi
    private let buffer: PcmBuffer
    private let sampleRate: Int
    private let callbacks: Callbacks
    private let clock: any VoiceClock

    private var task: Task<Void, Never>?
    private var wake: AsyncStream<Void>.Continuation?
    private var dictationId: String?
    private var chunkSamples = 0
    private var nextSeq = 0
    /// Highest seq the agent answered 200 for: what `finish` reports.
    private var accepted = -1
    private var closed = false
    private var closedAt: TimeInterval?
    private var serverCap = false
    private var restarts = 0
    private var resyncs = 0
    private var latched = false
    private var cancelled = false
    private var isLagging = false

    init(
        api: any VoiceApi,
        buffer: PcmBuffer,
        sampleRate: Int,
        callbacks: Callbacks,
        clock: any VoiceClock = SystemVoiceClock()
    ) {
        self.api = api
        self.buffer = buffer
        self.sampleRate = sampleRate
        self.callbacks = callbacks
        self.clock = clock
    }

    var isRunning: Bool { task != nil }

    func start() {
        launch()
    }

    /// New samples are in the buffer.
    func onAudio() {
        wake?.yield()
    }

    /// Recording stopped: upload the tail and transcribe.
    func close() {
        if !closed {
            closed = true
            closedAt = clock.now()
        }
        wake?.yield()
    }

    /// Resume after a retryable failure, from the buffered audio. A live
    /// dictation continues where it stopped (or re-asks `finish`, whose result
    /// the agent keeps); a latched or never-opened one starts over.
    @discardableResult
    func retry() -> Bool {
        guard task == nil, !cancelled else { return false }
        restarts = 0
        resyncs = 0
        if latched || dictationId == nil {
            if let dictationId { api.discard(dictationId: dictationId) }
            dictationId = nil
            nextSeq = 0
            accepted = -1
            latched = false
            serverCap = false
        }
        closed = true
        closedAt = clock.now()
        launch()
        return true
    }

    /// Abort everything and delete the dictation on the agent; never reports.
    func cancel() {
        guard !cancelled else { return }
        cancelled = true
        task?.cancel()
        wake?.finish()
        if let dictationId { api.discard(dictationId: dictationId) }
        dictationId = nil
    }

    private func launch() {
        let (stream, continuation) = AsyncStream.makeStream(of: Void.self, bufferingPolicy: .bufferingNewest(1))
        wake = continuation
        task = Task { [weak self] in
            await self?.run(stream)
            self?.task = nil
        }
    }

    private func run(_ wakes: AsyncStream<Void>) async {
        var iterator = wakes.makeAsyncIterator()
        do {
            if dictationId == nil { try await open() }
            while true {
                try checkCancelled()
                let final = closed || serverCap
                let sealed = buffer.sealedChunks(chunkSamples: chunkSamples, final: final)
                if !serverCap && nextSeq < sealed {
                    try await send(nextSeq)
                    continue
                }
                if final {
                    switch try await finishLoop() {
                    case .resync:
                        continue
                    case .text(let text):
                        try checkCancelled()
                        callbacks.done(text)
                        return
                    }
                }
                _ = await iterator.next()
            }
        } catch let fatal as Fatal {
            if !cancelled { callbacks.failed(fatal.failure) }
        } catch {
            if !cancelled && !(error is CancellationError) {
                callbacks.failed(UploadFailure(code: VoiceCodes.generic, retryable: true, message: error.localizedDescription))
            }
        }
    }

    private func open() async throws {
        var attempt = 0
        var firstFailure: TimeInterval?
        while true {
            try checkCancelled()
            // An unstructured task, so cancelling the pump never abandons a
            // create midway: the id it returns must reach a delete, or the
            // dictation would hold one of the agent's two slots.
            let api = api
            let creation = Task { try await Self.withTimeout(Self.createTimeout) { try await api.create() } }
            let result = await creation.result
            if cancelled {
                if case .success(let opened?) = result { api.discard(dictationId: opened.dictationId) }
                throw CancellationError()
            }
            let opened: VoiceDictationOpened
            switch result {
            case .success(let value?):
                opened = value
            case .success(nil):
                firstFailure = firstFailure ?? clock.now()
                try await recoverTransient(Classified.timeout, since: firstFailure, attempt: &attempt)
                continue
            case .failure(let error):
                let failure = classify(error)
                guard failure.transient else { throw fatal(failure) }
                firstFailure = firstFailure ?? clock.now()
                try await recoverTransient(failure, since: firstFailure, attempt: &attempt)
                continue
            }
            let bytes = opened.sampleRate == sampleRate
                ? Pcm16.chunkBytes(sampleRate: opened.sampleRate, maxChunkBytes: opened.maxChunkBytes)
                : nil
            guard let bytes else {
                api.discard(dictationId: opened.dictationId)
                throw Fatal(failure: UploadFailure(code: VoiceCodes.generic, retryable: false, message: nil))
            }
            chunkSamples = bytes / 2
            buffer.shrinkCapacity(opened.maxSeconds * opened.sampleRate)
            dictationId = opened.dictationId
            setLagging(false)
            if buffer.isFull && !closed { callbacks.stopCapture() }
            return
        }
    }

    private func send(_ seq: Int) async throws {
        let id = try requireDictation()
        let payload = buffer.payload(seq: seq, chunkSamples: chunkSamples)
        var attempt = 0
        var firstFailure: TimeInterval?
        let api = api
        while true {
            let failure: Classified
            do {
                let answered = try await Self.withTimeout(Self.audioTimeout) {
                    try await api.audio(dictationId: id, seq: seq, pcm16B64: payload)
                    return true
                }
                if answered == true {
                    accepted = seq
                    nextSeq = seq + 1
                    setLagging(false)
                    return
                }
                failure = .timeout
            } catch {
                try checkCancelled()
                failure = classify(error)
            }
            if failure.transient {
                firstFailure = firstFailure ?? clock.now()
                try await recoverTransient(failure, since: firstFailure, attempt: &attempt)
                continue
            }
            switch failure.code {
            case VoiceCodes.dictationNotFound:
                try await restart()
                return
            case VoiceCodes.seqGap:
                try resync(failure.expectedSeq)
                return
            case VoiceCodes.tooLong:
                serverCap = true
                callbacks.stopCapture()
                return
            case VoiceCodes.transcriptionFailed:
                latched = true
                throw fatal(failure)
            default:
                throw fatal(failure)
            }
        }
    }

    private func finishLoop() async throws -> FinishOutcome {
        let id = try requireDictation()
        let deadline = clock.now() + Self.finishDeadline
        var attempt = 0
        var firstFailure: TimeInterval?
        let api = api
        while true {
            try checkCancelled()
            if clock.now() > deadline {
                throw Fatal(failure: UploadFailure(code: VoiceCodes.transcriptionTimeout, retryable: true, message: nil))
            }
            let askedAt = clock.now()
            let lastSeq = accepted
            let failure: Classified
            do {
                if let result = try await Self.withTimeout(Self.finishTimeout, {
                    try await api.finish(dictationId: id, lastSeq: lastSeq)
                }) {
                    setLagging(false)
                    if result.state == "done" {
                        return .text((result.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines))
                    }
                    attempt = 0
                    firstFailure = nil
                    // `transcribing` normally follows the agent's own 20 s
                    // wait; an instant answer must not become a hot loop.
                    let elapsed = clock.now() - askedAt
                    if elapsed < Self.minFinishPoll {
                        try await clock.sleep(seconds: Self.minFinishPoll - elapsed)
                    }
                    continue
                }
                failure = .timeout
            } catch {
                try checkCancelled()
                failure = classify(error)
            }
            if failure.transient {
                firstFailure = firstFailure ?? clock.now()
                try await recoverTransient(failure, since: firstFailure, attempt: &attempt)
                continue
            }
            switch failure.code {
            case VoiceCodes.seqGap:
                try resync(failure.expectedSeq)
                return .resync
            case VoiceCodes.dictationNotFound:
                try await restart()
                return .resync
            case VoiceCodes.transcriptionFailed:
                latched = true
                throw fatal(failure)
            default:
                throw fatal(failure)
            }
        }
    }

    private func resync(_ expected: Int?) throws {
        resyncs += 1
        guard resyncs <= Self.maxResyncs, let expected, expected >= 0, expected <= nextSeq else {
            throw Fatal(failure: UploadFailure(code: VoiceCodes.seqGap, retryable: false, message: nil))
        }
        nextSeq = expected
        accepted = expected - 1
    }

    private func restart() async throws {
        restarts += 1
        guard restarts <= Self.maxRestarts else {
            throw Fatal(failure: UploadFailure(code: VoiceCodes.dictationNotFound, retryable: true, message: nil))
        }
        if let dictationId { api.discard(dictationId: dictationId) }
        dictationId = nil
        nextSeq = 0
        accepted = -1
        resyncs = 0
        serverCap = false
        try await open()
    }

    private func recoverTransient(_ failure: Classified, since firstFailure: TimeInterval?, attempt: inout Int) async throws {
        if let closedAt, let firstFailure, clock.now() - max(firstFailure, closedAt) > Self.stallLimit {
            throw Fatal(failure: UploadFailure(code: VoiceCodes.network, retryable: true, message: failure.message))
        }
        setLagging(true)
        try await backoff(attempt)
        attempt += 1
    }

    private func classify(_ error: any Error) -> Classified {
        if let voice = error as? VoiceApiError {
            let serverTrouble = (voice.status ?? 0) >= 500 && (voice.code == nil || voice.code == VoiceCodes.busy)
            return Classified(
                transient: voice.network || serverTrouble,
                status: voice.status,
                code: voice.code,
                message: voice.detailMessage ?? voice.message,
                expectedSeq: voice.expectedSeq
            )
        }
        if error is URLError {
            return Classified(transient: true, status: nil, code: nil, message: error.localizedDescription, expectedSeq: nil)
        }
        return Classified(transient: false, status: nil, code: nil, message: error.localizedDescription, expectedSeq: nil)
    }

    private func fatal(_ failure: Classified) -> Fatal {
        Fatal(failure: UploadFailure(
            code: failure.code ?? (failure.transient ? VoiceCodes.network : VoiceCodes.generic),
            retryable: Self.isRetryable(failure),
            message: failure.message
        ))
    }

    private static func isRetryable(_ failure: Classified) -> Bool {
        if failure.transient { return true }
        switch failure.code {
        case VoiceCodes.busy, VoiceCodes.transcriptionFailed, VoiceCodes.dictationNotFound:
            return true
        case VoiceCodes.notReady, VoiceCodes.unsupported, VoiceCodes.badAudio, VoiceCodes.chunkTooLarge,
             VoiceCodes.dictationFinished, VoiceCodes.seqGap, VoiceCodes.tooLong:
            return false
        default:
            return (failure.status ?? 0) >= 500
        }
    }

    private func backoff(_ attempt: Int) async throws {
        let base = Self.backoffSeconds[min(attempt, Self.backoffSeconds.count - 1)]
        let jitter = base * 0.2 * (clock.random() * 2 - 1)
        try await clock.sleep(seconds: base + jitter)
        try checkCancelled()
    }

    private func setLagging(_ lagging: Bool) {
        guard isLagging != lagging else { return }
        isLagging = lagging
        callbacks.lagging(lagging)
    }

    private func requireDictation() throws -> String {
        guard let dictationId else {
            throw Fatal(failure: UploadFailure(code: VoiceCodes.generic, retryable: true, message: nil))
        }
        return dictationId
    }

    private func checkCancelled() throws {
        if cancelled || Task.isCancelled { throw CancellationError() }
    }

    /// The operation's value, or nil when `seconds` pass first (the
    /// operation is then cancelled).
    nonisolated static func withTimeout<T: Sendable>(
        _ seconds: Double,
        _ operation: @escaping @Sendable () async throws -> T
    ) async throws -> T? {
        try await withThrowingTaskGroup(of: T?.self) { group in
            group.addTask { try await operation() }
            group.addTask {
                try await Task.sleep(nanoseconds: UInt64(seconds * 1e9))
                return nil
            }
            defer { group.cancelAll() }
            return try await group.next() ?? nil
        }
    }
}
