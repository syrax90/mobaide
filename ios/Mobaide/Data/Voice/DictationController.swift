import Foundation
import Observation

/// Why recording stopped on its own; the audio so far is still transcribed.
enum InterruptReason: Equatable, Sendable {
    case background
    case call
    case device
    case limit
}

enum DictationState: Equatable, Sendable {
    case idle
    case starting
    /// `remainingSeconds` is set once 15 or fewer remain; `lagging` means
    /// uploads are behind (network trouble) while recording carries on.
    case recording(elapsedMs: Int, level: Float, remainingSeconds: Int?, lagging: Bool)
    case transcribing(interruptedBy: InterruptReason?, lagging: Bool)
    case result(String)
    case empty
    /// `message` is the agent's raw text, shown only behind "Details";
    /// `hasAudio` means Retry needs no new recording.
    case failed(code: String, retryable: Bool, message: String?, hasAudio: Bool)

    var isActive: Bool {
        switch self {
        case .starting, .recording, .transcribing: return true
        default: return false
        }
    }

    var isResult: Bool {
        if case .result = self { return true }
        return false
    }
}

/// Callbacks from the capture thread.
struct VoiceRecorderSink: Sendable {
    let samples: @Sendable ([Int16]) -> Void
    /// 0…1
    let level: @Sendable (Float) -> Void
    let interrupted: @Sendable (InterruptReason) -> Void
}

/// Platform audio capture at the agent's rate as PCM16. `start` throws a
/// `VoiceRecorderError`; `stop` is idempotent and delivers every pending
/// sample before it returns.
@MainActor
protocol VoiceRecorder: AnyObject {
    func start(sampleRate: Int, sink: VoiceRecorderSink) async throws
    func stop() async
}

struct VoiceRecorderError: Error, Equatable, Sendable {
    /// `VoiceCodes.micPermissionDenied` or `VoiceCodes.micUnavailable`.
    let code: String
    var message: String?
}

/// One dictation at a time, as the UI sees it: microphone → buffer → uploader
/// → transcript. Elapsed time is counted in samples, so the countdown and the
/// agent's `max_seconds` agree exactly.
@MainActor
@Observable
final class DictationController {
    private(set) var state: DictationState = .idle {
        didSet {
            if state != oldValue { onTransition?(state) }
        }
    }

    /// Called on every state change (the owner inserts a `.result` and
    /// announces recording to VoiceOver).
    @ObservationIgnored var onTransition: ((DictationState) -> Void)?

    @ObservationIgnored private let api: any VoiceApi
    @ObservationIgnored private let makeRecorder: @MainActor () -> any VoiceRecorder
    @ObservationIgnored private let sampleRate: Int
    @ObservationIgnored private let maxSeconds: Int
    @ObservationIgnored private let clock: any VoiceClock

    /// Bumped by every new dictation and every cancel; stale callbacks compare it.
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var recorder: (any VoiceRecorder)?
    @ObservationIgnored private var buffer: PcmBuffer?
    @ObservationIgnored private var uploader: DictationUploader?
    @ObservationIgnored private var level: Float = 0
    @ObservationIgnored private var lagging = false
    @ObservationIgnored private var disposed = false

    private static let countdownSeconds = 15

    init(
        api: any VoiceApi,
        makeRecorder: @escaping @MainActor () -> any VoiceRecorder,
        sampleRate: Int,
        maxSeconds: Int,
        clock: any VoiceClock = SystemVoiceClock()
    ) {
        self.api = api
        self.makeRecorder = makeRecorder
        self.sampleRate = sampleRate
        self.maxSeconds = maxSeconds
        self.clock = clock
    }

    /// The mic button: start, stop, or abandon a start that is still opening.
    func toggle() {
        switch state {
        case .recording: stop()
        case .starting: cancel()
        case .transcribing: break
        default: start()
        }
    }

    func start() {
        guard !disposed, !state.isActive else { return }
        discard()
        generation += 1
        let generation = generation
        let buffer = PcmBuffer(capacitySamples: maxSeconds * sampleRate)
        let uploader = DictationUploader(
            api: api,
            buffer: buffer,
            sampleRate: sampleRate,
            callbacks: DictationUploader.Callbacks(
                lagging: { [weak self] lagging in
                    guard let self, generation == self.generation else { return }
                    self.lagging = lagging
                    self.publishProgress()
                },
                stopCapture: { [weak self] in
                    guard let self, generation == self.generation else { return }
                    self.stop(reason: .limit)
                },
                done: { [weak self] text in
                    guard let self, generation == self.generation else { return }
                    self.finish(with: text.isEmpty ? .empty : .result(text))
                },
                failed: { [weak self] failure in
                    guard let self, generation == self.generation else { return }
                    self.fail(failure)
                }
            ),
            clock: clock
        )
        let recorder = makeRecorder()
        self.buffer = buffer
        self.uploader = uploader
        self.recorder = recorder
        level = 0
        lagging = false
        state = .starting
        // The dictation opens while the microphone does, so the first words
        // are buffered instead of waiting for the round trip.
        uploader.start()
        let sink = makeSink(generation: generation, buffer: buffer, uploader: uploader)
        let sampleRate = sampleRate
        Task { [weak self] in
            do {
                try await recorder.start(sampleRate: sampleRate, sink: sink)
            } catch {
                guard let self, generation == self.generation, self.state == .starting else { return }
                // An upload failure may already have ended this dictation
                // while the microphone was opening; that is the one to show.
                self.recorder = nil
                uploader.cancel()
                buffer.release()
                self.buffer = nil
                self.uploader = nil
                self.state = .failed(
                    code: (error as? VoiceRecorderError)?.code ?? VoiceCodes.micUnavailable,
                    retryable: false,
                    message: (error as? VoiceRecorderError)?.message,
                    hasAudio: false
                )
                return
            }
            guard let self else { return }
            if generation != self.generation {
                await recorder.stop()
                return
            }
            if self.state == .starting { self.publishRecording() }
        }
    }

    /// Stop recording and transcribe what was captured.
    func stop(reason: InterruptReason? = nil) {
        switch state {
        case .starting:
            cancel()
            return
        case .recording:
            break
        default:
            return
        }
        let generation = generation
        state = .transcribing(interruptedBy: reason, lagging: lagging)
        let recorder = recorder
        self.recorder = nil
        Task { [weak self] in
            await recorder?.stop()
            guard let self, generation == self.generation else { return }
            self.uploader?.close()
        }
    }

    /// Throw the recording away, on the agent too.
    func cancel() {
        generation += 1
        discard()
        state = .idle
    }

    /// Retry a failed upload from the buffered audio.
    func retry() {
        guard case .failed(_, let retryable, _, let hasAudio) = state, retryable, hasAudio else { return }
        if uploader?.retry() == true {
            lagging = false
            state = .transcribing(interruptedBy: nil, lagging: false)
        }
    }

    /// Leave a result, an empty result or a failure.
    func dismiss() {
        switch state {
        case .result, .empty, .failed: cancel()
        default: break
        }
    }

    /// Leaving the session or switching agents.
    func dispose() {
        guard !disposed else { return }
        cancel()
        disposed = true
    }

    private func makeSink(generation: Int, buffer: PcmBuffer, uploader: DictationUploader) -> VoiceRecorderSink {
        VoiceRecorderSink(
            samples: { [weak self] samples in
                // Copied into the buffer on the capture thread.
                buffer.append(samples)
                Task { @MainActor [weak self] in
                    guard let self, generation == self.generation else { return }
                    uploader.onAudio()
                    guard case .recording = self.state else { return }
                    if buffer.isFull {
                        self.stop(reason: .limit)
                    } else {
                        self.publishRecording()
                    }
                }
            },
            level: { [weak self] level in
                Task { @MainActor [weak self] in
                    guard let self, generation == self.generation else { return }
                    self.level = level
                }
            },
            interrupted: { [weak self] reason in
                Task { @MainActor [weak self] in
                    guard let self, generation == self.generation else { return }
                    self.stop(reason: reason)
                }
            }
        )
    }

    private func fail(_ failure: UploadFailure) {
        let generation = generation
        let recorder = recorder
        self.recorder = nil
        Task { [weak self] in
            await recorder?.stop()
            guard let self, generation == self.generation else { return }
            self.state = .failed(
                code: failure.code,
                retryable: failure.retryable,
                message: failure.message,
                hasAudio: (self.buffer?.sampleCount ?? 0) > 0
            )
        }
    }

    private func finish(with state: DictationState) {
        // The agent drops a finished dictation by itself; no delete needed.
        buffer?.release()
        buffer = nil
        uploader = nil
        self.state = state
    }

    private func discard() {
        if let recorder {
            Task { await recorder.stop() }
        }
        recorder = nil
        uploader?.cancel()
        uploader = nil
        buffer?.release()
        buffer = nil
        level = 0
        lagging = false
    }

    private func publishProgress() {
        switch state {
        case .recording:
            publishRecording()
        case .transcribing(let reason, let current) where current != lagging:
            state = .transcribing(interruptedBy: reason, lagging: lagging)
        default:
            break
        }
    }

    private func publishRecording() {
        guard let buffer else { return }
        let count = buffer.sampleCount
        let elapsedMs = count * 1000 / sampleRate
        let left = Double(buffer.capacitySamples - count) / Double(sampleRate)
        state = .recording(
            elapsedMs: elapsedMs,
            level: level,
            remainingSeconds: left <= Double(Self.countdownSeconds) ? max(0, Int(left.rounded(.up))) : nil,
            lagging: lagging
        )
    }
}
