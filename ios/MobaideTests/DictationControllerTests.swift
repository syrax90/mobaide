import XCTest
@testable import Mobaide

/// `DictationController`'s transitions with a fake microphone and the scripted
/// agent: idle → starting → recording → transcribing → (cleaning →) result |
/// empty | failed, plus cancel, retry, dismiss and dispose.
@MainActor
final class DictationControllerTests: XCTestCase {
    private var api: FakeVoiceApi!
    private var recorder: FakeVoiceRecorder!

    override func setUp() async throws {
        try await super.setUp()
        api = FakeVoiceApi()
        api.configure(maxChunkBytes: 32_000)
        recorder = FakeVoiceRecorder()
    }

    private func controller(
        maxSeconds: Int = 120,
        cleaner: @escaping @MainActor () -> TranscriptCleaner? = { nil }
    ) -> DictationController {
        let recorder = recorder!
        return DictationController(
            api: api,
            makeRecorder: { recorder },
            sampleRate: 16_000,
            maxSeconds: maxSeconds,
            clock: FakeVoiceClock(),
            cleaner: cleaner
        )
    }

    private func recording(
        maxSeconds: Int = 120,
        cleaner: @escaping @MainActor () -> TranscriptCleaner? = { nil }
    ) async -> DictationController {
        let dictation = controller(maxSeconds: maxSeconds, cleaner: cleaner)
        dictation.start()
        await waitUntil { if case .recording = dictation.state { return true } else { return false } }
        return dictation
    }

    /// A recorded second, stopped and transcribed as "hello world", with
    /// `cleaner` asked to clean it up; waits until the cleanup has an answer
    /// or is still running.
    private func cleaning(_ cleaner: FakeCleaner) async -> DictationController {
        let dictation = await recording(cleaner: { cleaner.clean })
        recorder.speak(seconds: 1)
        await waitUntil { self.elapsedMs(dictation) == 1_000 }
        dictation.stop()
        await waitUntil { !dictation.state.isActive || cleaner.entered }
        return dictation
    }

    private func elapsedMs(_ dictation: DictationController) -> Int? {
        if case .recording(let elapsed, _, _, _) = dictation.state { return elapsed }
        return nil
    }

    func testRecordsTranscribesOnStopAndHandsTheTextBackAsAResult() async {
        var transitions: [DictationState] = []
        let dictation = controller()
        dictation.onTransition = { transitions.append($0) }
        dictation.start()
        await waitUntil { if case .recording = dictation.state { return true } else { return false } }
        recorder.sink?.level(0.4)
        await waitUntil { self.api.log.count >= 1 }
        recorder.speak(seconds: 1)
        await waitUntil { self.elapsedMs(dictation) == 1_000 }

        XCTAssertEqual(dictation.state, .recording(elapsedMs: 1_000, level: 0.4, remainingSeconds: nil, lagging: false))

        dictation.toggle()
        XCTAssertEqual(dictation.state, .transcribing(interruptedBy: nil, lagging: false))
        await waitUntil { dictation.state.isResult }

        XCTAssertEqual(dictation.state, .result("hello world"))
        XCTAssertEqual(transitions.first, .starting)
        XCTAssertEqual(transitions.last, .result("hello world"))
        XCTAssertEqual(recorder.sampleRate, 16_000)
        XCTAssertEqual(recorder.stops, 1)
        XCTAssertEqual(api.log, ["create", "audio d1#0", "finish d1@0"])
    }

    func testCountsDownTheLast15sFromTheSamplesRecorded() async {
        let dictation = await recording(maxSeconds: 20)

        recorder.speak(seconds: 4)
        await waitUntil { self.elapsedMs(dictation) == 4_000 }
        XCTAssertEqual(dictation.state, .recording(elapsedMs: 4_000, level: 0, remainingSeconds: nil, lagging: false))

        recorder.speak(seconds: 2)
        await waitUntil { self.elapsedMs(dictation) == 6_000 }
        XCTAssertEqual(dictation.state, .recording(elapsedMs: 6_000, level: 0, remainingSeconds: 14, lagging: false))
    }

    func testStopsByItselfAtTheLimitAndStillTranscribes() async {
        let finish = HeldReply<VoiceFinishResult>()
        api.enqueueFinish(.hold(finish))
        let dictation = await recording(maxSeconds: 2)

        recorder.speak(seconds: 2)
        await waitUntil { dictation.state == .transcribing(interruptedBy: .limit, lagging: false) }

        XCTAssertEqual(dictation.state, .transcribing(interruptedBy: .limit, lagging: false))
        await waitUntil { finish.entered }
        finish.resolve(VoiceFinishResult(state: "done", text: "hello world"))
        await waitUntil { dictation.state.isResult }
        XCTAssertEqual(dictation.state, .result("hello world"))
    }

    func testAnInterruptionTranscribesWhatWasRecorded() async {
        let finish = HeldReply<VoiceFinishResult>()
        api.enqueueFinish(.hold(finish))
        let dictation = await recording()
        recorder.speak(seconds: 1)
        await waitUntil { self.elapsedMs(dictation) == 1_000 }

        recorder.sink?.interrupted(.call)
        await waitUntil { dictation.state == .transcribing(interruptedBy: .call, lagging: false) }

        XCTAssertEqual(dictation.state, .transcribing(interruptedBy: .call, lagging: false))
        await waitUntil { finish.entered }
        XCTAssertEqual(recorder.stops, 1)
        finish.resolve(VoiceFinishResult(state: "done", text: "hello world"))
        await waitUntil { dictation.state.isResult }
        XCTAssertEqual(dictation.state, .result("hello world"))
    }

    func testARefusedMicrophoneFailsWithoutAudioAndDeletesTheOpenedDictation() async {
        let gate = HeldReply<Bool>()
        recorder.startGate = gate
        recorder.startFailure = VoiceRecorderError(code: VoiceCodes.micPermissionDenied, message: "denied")
        let dictation = controller()

        dictation.start()
        await waitUntil { self.api.log == ["create"] && gate.entered }
        try? await Task.sleep(for: .milliseconds(50))
        gate.resolve(true)
        await waitUntil { if case .failed = dictation.state { return true } else { return false } }

        XCTAssertEqual(
            dictation.state,
            .failed(code: VoiceCodes.micPermissionDenied, retryable: false, message: "denied", hasAudio: false)
        )
        XCTAssertEqual(api.log, ["create", "remove d1"])
    }

    func testAnUploadFailureKeepsTheAudioSoRetryNeedsNoNewRecording() async {
        api.enqueueFinish(.fail(voiceRefusal(503, VoiceCodes.transcriptionFailed)))
        let dictation = await recording()
        recorder.speak(seconds: 1)
        await waitUntil { self.elapsedMs(dictation) == 1_000 }
        dictation.stop()
        await waitUntil { if case .failed = dictation.state { return true } else { return false } }

        XCTAssertEqual(
            dictation.state,
            .failed(code: VoiceCodes.transcriptionFailed, retryable: true, message: "HTTP 503", hasAudio: true)
        )

        try? await Task.sleep(for: .milliseconds(50))
        dictation.retry()
        XCTAssertEqual(dictation.state, .transcribing(interruptedBy: nil, lagging: false))
        await waitUntil { dictation.state.isResult }

        XCTAssertEqual(dictation.state, .result("hello world"))
        XCTAssertEqual(Array(api.log.dropFirst(3)), ["remove d1", "create", "audio d2#0", "finish d2@0"])
    }

    func testRetryIsIgnoredForAFailureThatCannotSucceedAgain() async {
        api.enqueueAudio(.fail(voiceRefusal(422, VoiceCodes.badAudio)))
        let dictation = await recording()
        recorder.speak(seconds: 1)
        await waitUntil { if case .failed = dictation.state { return true } else { return false } }
        let failed = dictation.state

        dictation.retry()
        try? await Task.sleep(for: .milliseconds(50))

        XCTAssertEqual(dictation.state, failed)
        XCTAssertEqual(api.count("create"), 1)
    }

    func testCancelReturnsToIdleDeletesTheDictationAndTranscribesNothing() async {
        let dictation = await recording()
        recorder.speak(seconds: 1)
        await waitUntil { self.api.log.count == 2 }

        dictation.cancel()
        await waitUntil { self.recorder.stops == 1 }
        try? await Task.sleep(for: .milliseconds(50))

        XCTAssertEqual(dictation.state, .idle)
        XCTAssertEqual(recorder.stops, 1)
        XCTAssertEqual(api.log, ["create", "audio d1#0", "remove d1"])
    }

    func testToggleWhileTheMicrophoneIsStillOpeningAbandonsTheStart() async {
        let gate = HeldReply<Bool>()
        recorder.startGate = gate
        let dictation = controller()
        dictation.start()
        await waitUntil { gate.entered && self.api.log == ["create"] }
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(dictation.state, .starting)

        dictation.toggle()
        gate.resolve(true)
        await waitUntil { self.recorder.stops >= 1 }

        XCTAssertEqual(dictation.state, .idle)
        XCTAssertEqual(api.log, ["create", "remove d1"])
    }

    func testDisposeCancelsAndStartsNothingAfterwards() async {
        let dictation = await recording()
        recorder.speak(seconds: 1)
        await waitUntil { self.api.log.count == 2 }

        dictation.dispose()
        dictation.start()
        try? await Task.sleep(for: .milliseconds(50))

        XCTAssertEqual(dictation.state, .idle)
        XCTAssertEqual(api.count("create"), 1)
        XCTAssertEqual(api.log.last, "remove d1")
    }

    func testCleansTheTranscriptUpBeforeHandingItBack() async {
        let held = HeldReply<String>()
        let cleaner = FakeCleaner(.hold(held))
        let dictation = await cleaning(cleaner)

        XCTAssertEqual(dictation.state, .cleaning("hello world"))
        held.resolve("Hello, world.")
        await waitUntil { dictation.state.isResult }

        XCTAssertEqual(dictation.state, .result("Hello, world."))
        XCTAssertEqual(cleaner.calls, ["hello world"])
    }

    func testACleanupThatComesBackUnchangedOrEmptyHandsBackAPlainResult() async {
        for answer in ["hello world", "  hello world\n", "  "] {
            let dictation = await cleaning(FakeCleaner(.ok(answer)))
            await waitUntil { dictation.state.isResult }

            XCTAssertEqual(dictation.state, .result("hello world"), "answer: \(answer)")
            dictation.dismiss()
        }
    }

    func testACleanupThatFailsHandsOverTheTranscriptAsHeardSayingWhy() async {
        let failures: [(any Error, VoiceCleanupError)] = [
            (
                voiceRefusal(503, VoiceCodes.cleanupOutdated, detailMessage: "Run claude update."),
                VoiceCleanupError(code: VoiceCodes.cleanupOutdated, message: "Run claude update.")
            ),
            (voiceRefusal(502, nil), VoiceCleanupError(code: VoiceCodes.generic, message: "HTTP 502")),
            (voiceNetworkError(), VoiceCleanupError(code: VoiceCodes.network, message: "offline")),
            (Boom(), VoiceCleanupError(code: VoiceCodes.generic, message: "boom")),
        ]
        for (error, expected) in failures {
            let dictation = await cleaning(FakeCleaner(.fail(error)))
            await waitUntil { dictation.state.isResult }

            XCTAssertEqual(dictation.state, .result("hello world", cleanupError: expected))
            dictation.dismiss()
        }
    }

    func testCancelWhileCleaningUpReturnsToIdleAndAbortsTheRequest() async {
        let held = HeldReply<String>()
        let dictation = await cleaning(FakeCleaner(.hold(held)))
        await waitUntil { held.entered }

        dictation.cancel()
        await waitUntil { held.aborted }

        XCTAssertTrue(held.aborted)
        XCTAssertEqual(dictation.state, .idle)
    }

    func testAnAbandonedCleanupAnswerNeverReachesTheNextDictation() async {
        // A reply already on its way when the user cancels ignores the abort.
        let deaf = DeafCleaner()
        let dictation = await recording(cleaner: { deaf.clean })
        recorder.speak(seconds: 1)
        await waitUntil { self.elapsedMs(dictation) == 1_000 }
        dictation.stop()
        await waitUntil { deaf.count == 1 }
        dictation.cancel()
        dictation.start()
        await waitUntil { if case .recording = dictation.state { return true } else { return false } }
        recorder.speak(seconds: 1)
        await waitUntil { self.elapsedMs(dictation) == 1_000 }
        dictation.stop()
        await waitUntil { deaf.count == 2 }

        deaf.answer(0, "Stale.")
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(dictation.state, .cleaning("hello world"))

        deaf.answer(1, "Fresh.")
        await waitUntil { dictation.state.isResult }
        XCTAssertEqual(dictation.state, .result("Fresh."))
    }

    func testTheCleanupSettingIsReadWhenTheTranscriptArrivesNotWhenRecordingStarts() async {
        let cleaner = FakeCleaner(.ok("Hello, world."))
        let setting = CleanupSetting()
        let dictation = await recording(cleaner: { setting.on ? cleaner.clean : nil })
        recorder.speak(seconds: 1)
        await waitUntil { self.elapsedMs(dictation) == 1_000 }

        setting.on = true
        dictation.stop()
        await waitUntil { dictation.state.isResult }

        XCTAssertEqual(dictation.state, .result("Hello, world."))
    }
}

/// The session's AI agent on the agent's computer, answering `reply`; every
/// transcript is kept.
private final class FakeCleaner: @unchecked Sendable {
    private let lock = NSLock()
    private let reply: VoiceReply<String>
    private var texts: [String] = []

    init(_ reply: VoiceReply<String>) {
        self.reply = reply
    }

    var calls: [String] { lock.withLock { texts } }
    var entered: Bool { !calls.isEmpty }

    var clean: TranscriptCleaner {
        { [self] text in
            lock.withLock { texts.append(text) }
            switch reply {
            case .ok(let value): return value
            case .fail(let error): throw error
            case .hold(let held): return try await held.wait()
            }
        }
    }
}

/// A cleaner whose answers ignore cancellation, as one already on its way does.
private final class DeafCleaner: @unchecked Sendable {
    private let lock = NSLock()
    private var waiting: [CheckedContinuation<String, any Error>] = []

    var count: Int { lock.withLock { waiting.count } }

    var clean: TranscriptCleaner {
        { [self] _ in
            try await withCheckedThrowingContinuation { continuation in
                lock.withLock { waiting.append(continuation) }
            }
        }
    }

    func answer(_ index: Int, _ text: String) {
        let continuation = lock.withLock { waiting[index] }
        continuation.resume(returning: text)
    }
}

@MainActor
private final class CleanupSetting {
    var on = false
}

private struct Boom: LocalizedError {
    var errorDescription: String? { "boom" }
}
