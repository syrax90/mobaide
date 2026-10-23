import XCTest
@testable import Mobaide

/// `DictationController`'s transitions with a fake microphone and the scripted
/// agent: idle → starting → recording → transcribing → result | empty |
/// failed, plus cancel, retry, dismiss and dispose.
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

    private func controller(maxSeconds: Int = 120) -> DictationController {
        let recorder = recorder!
        return DictationController(
            api: api,
            makeRecorder: { recorder },
            sampleRate: 16_000,
            maxSeconds: maxSeconds,
            clock: FakeVoiceClock()
        )
    }

    private func recording(maxSeconds: Int = 120) async -> DictationController {
        let dictation = controller(maxSeconds: maxSeconds)
        dictation.start()
        await waitUntil { if case .recording = dictation.state { return true } else { return false } }
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

    func testAnEmptyTranscriptReadsAsEmpty() async {
        api.configure(text: "")
        let dictation = await recording()
        recorder.speak(seconds: 1)
        await waitUntil { self.elapsedMs(dictation) == 1_000 }

        dictation.stop()
        await waitUntil { dictation.state == .empty }

        XCTAssertEqual(dictation.state, .empty)
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

    func testDismissLeavesAResult() async {
        let dictation = await recording()
        recorder.speak(seconds: 1)
        await waitUntil { self.elapsedMs(dictation) == 1_000 }
        dictation.stop()
        await waitUntil { dictation.state.isResult }

        dictation.dismiss()

        XCTAssertEqual(dictation.state, .idle)
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
}
