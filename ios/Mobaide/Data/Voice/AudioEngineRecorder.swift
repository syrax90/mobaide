@preconcurrency import AVFoundation

/// The session is `.record` in `.measurement` mode (no voice processing that
/// would colour recognition) and is deactivated with
/// `.notifyOthersOnDeactivation`, so music paused by a dictation resumes.
@MainActor
final class AudioEngineRecorder: VoiceRecorder {
    private let core = AudioEngineCore()
    private var observers: [any NSObjectProtocol] = []
    private var running = false
    private var stopRequested = false

    func start(sampleRate: Int, sink: VoiceRecorderSink) async throws {
        guard !stopRequested else { return }
        try await Self.requirePermission()
        guard !stopRequested else { return }
        try await core.start(sampleRate: sampleRate, sink: sink)
        guard !stopRequested else { return }
        running = true
        observeInterruptions(sink: sink)
    }

    /// Always queues the core's stop, even while `start` is still opening:
    /// the shared queue then runs this recorder's start, its stop and only
    /// then the next recording's start.
    func stop() async {
        stopRequested = true
        if running {
            running = false
            for observer in observers {
                NotificationCenter.default.removeObserver(observer)
            }
            observers = []
        }
        await core.stop()
    }

    private func observeInterruptions(sink: VoiceRecorderSink) {
        let center = NotificationCenter.default
        let session = AVAudioSession.sharedInstance()
        observers.append(center.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: session,
            queue: .main
        ) { note in
            let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
            if raw.flatMap(AVAudioSession.InterruptionType.init(rawValue:)) == .began {
                sink.interrupted(.call)
            }
        })
        observers.append(center.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: session,
            queue: .main
        ) { note in
            let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
            if raw.flatMap(AVAudioSession.RouteChangeReason.init(rawValue:)) == .oldDeviceUnavailable {
                sink.interrupted(.device)
            }
        })
        observers.append(center.addObserver(
            forName: AVAudioSession.mediaServicesWereResetNotification,
            object: session,
            queue: .main
        ) { _ in
            sink.interrupted(.device)
        })
        // The engine stops itself when its I/O configuration changes.
        observers.append(center.addObserver(
            forName: .AVAudioEngineConfigurationChange,
            object: core.engine,
            queue: .main
        ) { _ in
            sink.interrupted(.device)
        })
    }

    private static func requirePermission() async throws {
        switch AVAudioApplication.shared.recordPermission {
        case .granted:
            return
        case .undetermined:
            if await AVAudioApplication.requestRecordPermission() { return }
            throw VoiceRecorderError(code: VoiceCodes.micPermissionDenied)
        default:
            throw VoiceRecorderError(code: VoiceCodes.micPermissionDenied)
        }
    }
}

/// The session and engine calls block (activating the session, building the
/// input node and starting the engine can wait on the audio server), so they
/// run on one serial queue rather than the main actor.
private final class AudioEngineCore: @unchecked Sendable {
    let engine = AVAudioEngine()
    /// Shared by every recorder: a new recording's start runs after the
    /// previous one's stop, so the old session deactivation can never land
    /// on top of a new recording.
    private static let queue = DispatchQueue(label: "com.mobaide.voice.engine", qos: .userInitiated)
    private var queue: DispatchQueue { Self.queue }
    // Confined to `queue`.
    private var started = false
    private var cancelled = false

    func start(sampleRate: Int, sink: VoiceRecorderSink) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
            queue.async {
                do {
                    try self.startOnQueue(sampleRate: sampleRate, sink: sink)
                    continuation.resume()
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    func stop() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            queue.async {
                self.cancelled = true
                if self.started {
                    self.started = false
                    self.engine.inputNode.removeTap(onBus: 0)
                    self.engine.stop()
                    Self.deactivate()
                }
                continuation.resume()
            }
        }
    }

    private func startOnQueue(sampleRate: Int, sink: VoiceRecorderSink) throws {
        guard !cancelled else { return }
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.record, mode: .measurement, options: [])
            try session.setActive(true)
        } catch {
            throw VoiceRecorderError(code: VoiceCodes.micUnavailable, message: error.localizedDescription)
        }
        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        guard
            inputFormat.sampleRate > 0,
            inputFormat.channelCount > 0,
            let target = AVAudioFormat(
                commonFormat: .pcmFormatFloat32,
                sampleRate: Double(sampleRate),
                channels: 1,
                interleaved: false
            ),
            let converter = AVAudioConverter(from: inputFormat, to: target)
        else {
            Self.deactivate()
            throw VoiceRecorderError(code: VoiceCodes.micUnavailable, message: "No usable microphone input.")
        }
        let tap = CaptureTap(converter: converter, target: target, sink: sink)
        input.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { buffer, _ in
            tap.process(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            Self.deactivate()
            throw VoiceRecorderError(code: VoiceCodes.micUnavailable, message: error.localizedDescription)
        }
        started = true
    }

    private static func deactivate() {
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}

/// Runs on the audio thread: converts each tap buffer and hands PCM16 on.
private final class CaptureTap: @unchecked Sendable {
    private let converter: AVAudioConverter
    private let target: AVAudioFormat
    private let sink: VoiceRecorderSink

    init(converter: AVAudioConverter, target: AVAudioFormat, sink: VoiceRecorderSink) {
        self.converter = converter
        self.target = target
        self.sink = sink
    }

    func process(_ buffer: AVAudioPCMBuffer) {
        let ratio = target.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return }
        let supplied = SuppliedFlag()
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, inputStatus in
            if supplied.value {
                inputStatus.pointee = .noDataNow
                return nil
            }
            supplied.value = true
            inputStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, out.frameLength > 0, let channel = out.floatChannelData?[0] else { return }
        let pcm = Pcm16.quantize(UnsafeBufferPointer(start: channel, count: Int(out.frameLength)))
        sink.level(Pcm16.meterLevel(pcm))
        sink.samples(pcm)
    }
}

/// The converter calls its input block synchronously inside `convert`; the
/// box only satisfies the block's `@Sendable` signature.
private final class SuppliedFlag: @unchecked Sendable {
    var value = false
}
