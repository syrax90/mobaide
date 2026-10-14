import Foundation
@testable import Mobaide

/// A request the test answers by hand. It throws `CancellationError` (as
/// `URLSession` does) once its caller is cancelled, and records that.
final class HeldReply<T: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<T, any Error>?
    private var ready: Result<T, any Error>?
    private var isEntered = false
    private var isAborted = false

    var entered: Bool { lock.withLock { isEntered } }
    var aborted: Bool { lock.withLock { isAborted } }

    func wait() async throws -> T {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (cont: CheckedContinuation<T, any Error>) in
                let now: Result<T, any Error>? = lock.withLock {
                    isEntered = true
                    if let ready { return ready }
                    continuation = cont
                    return nil
                }
                if let now {
                    cont.resume(with: now)
                } else if Task.isCancelled {
                    abort()
                }
            }
        } onCancel: {
            abort()
        }
    }

    func resolve(_ value: T) {
        settle(.success(value))
    }

    private func abort() {
        let pending: CheckedContinuation<T, any Error>? = lock.withLock {
            isAborted = true
            defer { continuation = nil }
            return continuation
        }
        pending?.resume(throwing: CancellationError())
    }

    private func settle(_ result: Result<T, any Error>) {
        let pending: CheckedContinuation<T, any Error>? = lock.withLock {
            defer { continuation = nil }
            if continuation == nil { ready = result }
            return continuation
        }
        pending?.resume(with: result)
    }
}

enum VoiceReply<T: Sendable>: @unchecked Sendable {
    case ok(T)
    case fail(any Error)
    case hold(HeldReply<T>)
}

func voiceNetworkError() -> VoiceApiError {
    VoiceApiError(status: nil, code: nil, message: "offline", network: true)
}

func voiceRefusal(_ status: Int, _ code: String?, detailMessage: String? = nil, expectedSeq: Int? = nil) -> VoiceApiError {
    VoiceApiError(
        status: status,
        code: code,
        message: "HTTP \(status)",
        detailMessage: detailMessage,
        expectedSeq: expectedSeq
    )
}

/// The agent's dictation API, scripted per method: queued replies first, then
/// the method's fallback. Every call lands in `log` (`create`, `audio d1#0`,
/// `finish d1@2`, `remove d1`) so a test reads the request order directly.
final class FakeVoiceApi: VoiceApi, @unchecked Sendable {
    private let lock = NSLock()
    private var entries: [String] = []
    private var sent: [String: String] = [:]
    private var created = 0
    private var createQueue: [VoiceReply<VoiceDictationOpened>] = []
    private var audioQueue: [VoiceReply<Void>] = []
    private var finishQueue: [VoiceReply<VoiceFinishResult>] = []
    private var audioDefault: VoiceReply<Void> = .ok(())
    private var finishDefault: VoiceReply<VoiceFinishResult>?
    private var limits = (sampleRate: 16_000, maxSeconds: 120, maxChunkBytes: 8)
    private var transcript = "hello world"

    var log: [String] { lock.withLock { entries } }

    func payload(_ key: String) -> String? { lock.withLock { sent[key] } }

    func count(_ entry: String) -> Int { log.filter { $0 == entry }.count }

    func configure(sampleRate: Int? = nil, maxSeconds: Int? = nil, maxChunkBytes: Int? = nil, text: String? = nil) {
        lock.withLock {
            if let sampleRate { limits.sampleRate = sampleRate }
            if let maxSeconds { limits.maxSeconds = maxSeconds }
            if let maxChunkBytes { limits.maxChunkBytes = maxChunkBytes }
            if let text { transcript = text }
        }
    }

    func opened(_ dictationId: String) -> VoiceDictationOpened {
        lock.withLock {
            VoiceDictationOpened(
                dictationId: dictationId,
                sampleRate: limits.sampleRate,
                maxSeconds: limits.maxSeconds,
                maxChunkBytes: limits.maxChunkBytes
            )
        }
    }

    func enqueueCreate(_ reply: VoiceReply<VoiceDictationOpened>) { lock.withLock { createQueue.append(reply) } }
    func enqueueAudio(_ replies: VoiceReply<Void>...) { lock.withLock { audioQueue.append(contentsOf: replies) } }
    func enqueueFinish(_ replies: VoiceReply<VoiceFinishResult>...) { lock.withLock { finishQueue.append(contentsOf: replies) } }
    func setAudioFallback(_ reply: VoiceReply<Void>) { lock.withLock { audioDefault = reply } }
    func setFinishFallback(_ reply: VoiceReply<VoiceFinishResult>?) { lock.withLock { finishDefault = reply } }

    func create() async throws -> VoiceDictationOpened {
        let reply: VoiceReply<VoiceDictationOpened> = lock.withLock {
            entries.append("create")
            created += 1
            if !createQueue.isEmpty { return createQueue.removeFirst() }
            return .ok(VoiceDictationOpened(
                dictationId: "d\(created)",
                sampleRate: limits.sampleRate,
                maxSeconds: limits.maxSeconds,
                maxChunkBytes: limits.maxChunkBytes
            ))
        }
        return try await answer(reply)
    }

    func audio(dictationId: String, seq: Int, pcm16B64: String) async throws {
        let reply: VoiceReply<Void> = lock.withLock {
            entries.append("audio \(dictationId)#\(seq)")
            sent["\(dictationId)#\(seq)"] = pcm16B64
            return audioQueue.isEmpty ? audioDefault : audioQueue.removeFirst()
        }
        try await answer(reply)
    }

    func finish(dictationId: String, lastSeq: Int) async throws -> VoiceFinishResult {
        let reply: VoiceReply<VoiceFinishResult> = lock.withLock {
            entries.append("finish \(dictationId)@\(lastSeq)")
            if !finishQueue.isEmpty { return finishQueue.removeFirst() }
            return finishDefault ?? .ok(VoiceFinishResult(state: "done", text: transcript))
        }
        return try await answer(reply)
    }

    func discard(dictationId: String) {
        lock.withLock { entries.append("remove \(dictationId)") }
    }

    private func answer<T: Sendable>(_ reply: VoiceReply<T>) async throws -> T {
        switch reply {
        case .ok(let value): return value
        case .fail(let error): throw error
        case .hold(let held): return try await held.wait()
        }
    }
}

/// Time only moves when the uploader sleeps; every sleep is recorded.
final class FakeVoiceClock: VoiceClock, @unchecked Sendable {
    private let lock = NSLock()
    private var current: TimeInterval = 0
    private var recorded: [Double] = []
    private var fixedRandom = 0.5

    var sleeps: [Double] { lock.withLock { recorded } }
    var elapsed: TimeInterval { lock.withLock { current } }

    func setRandom(_ value: Double) { lock.withLock { fixedRandom = value } }

    func now() -> TimeInterval { lock.withLock { current } }

    func sleep(seconds: Double) async throws {
        lock.withLock {
            recorded.append(seconds)
            current += seconds
        }
        try Task.checkCancellation()
    }

    func random() -> Double { lock.withLock { fixedRandom } }
}

@MainActor
final class RecordingUploaderCallbacks {
    private(set) var events: [String] = []
    private(set) var failure: UploadFailure?

    var callbacks: DictationUploader.Callbacks {
        DictationUploader.Callbacks(
            lagging: { [weak self] in self?.events.append("lagging \($0)") },
            stopCapture: { [weak self] in self?.events.append("stopCapture") },
            done: { [weak self] in self?.events.append("done \($0)") },
            failed: { [weak self] failure in
                self?.events.append("failed \(failure.code)")
                self?.failure = failure
            }
        )
    }
}

/// The microphone, driven by the test through `sink`.
@MainActor
final class FakeVoiceRecorder: VoiceRecorder {
    private(set) var sink: VoiceRecorderSink?
    private(set) var sampleRate: Int?
    private(set) var stops = 0
    var startFailure: (any Error)?
    var startGate: HeldReply<Bool>?

    func start(sampleRate: Int, sink: VoiceRecorderSink) async throws {
        self.sampleRate = sampleRate
        self.sink = sink
        if let startGate { _ = try await startGate.wait() }
        if let startFailure { throw startFailure }
    }

    func stop() async {
        stops += 1
    }

    func speak(seconds: Int, rate: Int = 16_000) {
        sink?.samples((0..<(seconds * rate)).map { Int16(($0 % 200) - 100) })
    }
}

/// `count` distinct, non-zero samples.
func voiceRamp(_ count: Int, from: Int = 1) -> [Int16] {
    (0..<count).map { Int16(from + $0) }
}
