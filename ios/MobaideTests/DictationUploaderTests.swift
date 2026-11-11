import XCTest
@testable import Mobaide

/// `DictationUploader` against a scripted agent and a virtual clock. The fake
/// agent answers `max_chunk_bytes: 8`, so a chunk is 4 samples.
@MainActor
final class DictationUploaderTests: XCTestCase {
    private var api: FakeVoiceApi!
    private var clock: FakeVoiceClock!
    private var recorded: RecordingUploaderCallbacks!
    private var buffer: PcmBuffer!

    override func setUp() async throws {
        try await super.setUp()
        api = FakeVoiceApi()
        clock = FakeVoiceClock()
        recorded = RecordingUploaderCallbacks()
        buffer = PcmBuffer(capacitySamples: 1_000)
    }

    private func uploader() -> DictationUploader {
        DictationUploader(api: api, buffer: buffer, sampleRate: 16_000, callbacks: recorded.callbacks, clock: clock)
    }

    private var settled: Bool {
        recorded.events.contains { $0.hasPrefix("done") || $0.hasPrefix("failed") }
    }

    /// Start over `samples` already recorded, stop recording, run to the end.
    @discardableResult
    private func uploadClosed(_ samples: Int) async -> DictationUploader {
        buffer.append(voiceRamp(samples))
        let upload = uploader()
        upload.start()
        upload.close()
        await waitUntil { self.settled }
        return upload
    }

    /// Long enough for a pump that is not blocked to make its next request.
    private func idle() async {
        try? await Task.sleep(for: .milliseconds(50))
    }

    func testUploadsFullChunksWhileRecordingThenTheTailThenFinishesWithTheLastSeq() async {
        buffer.append(voiceRamp(10))
        let upload = uploader()
        upload.start()
        await waitUntil { self.api.log.count == 3 }
        await idle()

        XCTAssertEqual(api.log, ["create", "audio d1#0", "audio d1#1"])

        upload.close()
        await waitUntil { self.settled }

        XCTAssertEqual(api.log, ["create", "audio d1#0", "audio d1#1", "audio d1#2", "finish d1@2"])
        XCTAssertEqual(api.payload("d1#2"), buffer.payload(seq: 2, chunkSamples: 4))
        XCTAssertEqual(recorded.events, ["done hello world"])
    }

    func testKeepsOneAudioRequestInFlightAndFinishesOnlyAfterTheLastIsAnswered() async {
        let first = HeldReply<Void>()
        api.enqueueAudio(.hold(first))
        buffer.append(voiceRamp(4))
        let upload = uploader()
        upload.start()
        await waitUntil { first.entered }

        buffer.append(voiceRamp(6, from: 100))
        upload.onAudio()
        upload.onAudio()
        upload.close()
        await idle()

        XCTAssertEqual(api.log, ["create", "audio d1#0"])

        first.resolve(())
        await waitUntil { self.settled }

        XCTAssertEqual(api.log, ["create", "audio d1#0", "audio d1#1", "audio d1#2", "finish d1@2"])
        XCTAssertEqual(recorded.events, ["done hello world"])
    }

    func testRetriesALostRequestWithBackoffAndJitterAndReportsLagging() async {
        clock.setRandom(1)
        for _ in 0..<5 { api.enqueueAudio(.fail(voiceNetworkError())) }

        await uploadClosed(4)

        XCTAssertEqual(clock.sleeps.map { ($0 * 1000).rounded() }, [600, 1_200, 2_400, 4_800, 4_800])
        XCTAssertEqual(api.count("audio d1#0"), 6)
        XCTAssertEqual(recorded.events, ["lagging true", "lagging false", "done hello world"])
    }

    func testRetriesAURLErrorAnUncoded5xxAndA503BusyLikeALostRequest() async {
        api.enqueueAudio(
            // An explicit description: a cold CI simulator stalled ~20 s resolving the localized one.
            .fail(URLError(.networkConnectionLost, userInfo: [NSLocalizedDescriptionKey: "connection lost"])),
            .fail(voiceRefusal(502, nil)),
            .fail(voiceRefusal(503, VoiceCodes.busy))
        )

        await uploadClosed(4)

        XCTAssertEqual(api.count("audio d1#0"), 4)
        XCTAssertEqual(recorded.events.last, "done hello world")
    }

    func testNeverGivesUpOnAStalledUploadWhileStillRecording() async {
        for _ in 0..<30 { api.enqueueAudio(.fail(voiceNetworkError())) }
        buffer.append(voiceRamp(4))
        let upload = uploader()
        upload.start()
        await waitUntil { self.api.count("audio d1#0") == 31 }
        await idle()

        XCTAssertGreaterThan(clock.elapsed, 60)
        XCTAssertEqual(recorded.events, ["lagging true", "lagging false"])
        upload.cancel()
    }

    func testFailsAStalledUpload30sAfterRecordingStoppedRetryably() async {
        api.setAudioFallback(.fail(voiceNetworkError()))

        await uploadClosed(4)

        XCTAssertEqual(recorded.failure, UploadFailure(code: VoiceCodes.network, retryable: true, message: "offline"))
        XCTAssertGreaterThan(clock.elapsed, 30)
        XCTAssertLessThanOrEqual(clock.elapsed, 34)
    }

    func testRetryContinuesALiveDictationWhereItStopped() async {
        api.enqueueAudio(.ok(()))
        api.setAudioFallback(.fail(voiceNetworkError()))
        let upload = await uploadClosed(8)
        XCTAssertEqual(recorded.failure?.code, VoiceCodes.network)
        api.setAudioFallback(.ok(()))
        await waitUntil { !upload.isRunning }

        XCTAssertTrue(upload.retry())
        await waitUntil { self.recorded.events.last == "done hello world" }

        XCTAssertEqual(api.log.filter { $0 != "audio d1#1" }, ["create", "audio d1#0", "finish d1@1"])
    }

    func testResendsFromTheExpectedSeqAfterASeqGap() async {
        api.enqueueAudio(.ok(()), .ok(()), .fail(voiceRefusal(409, VoiceCodes.seqGap, expectedSeq: 1)))

        await uploadClosed(12)

        XCTAssertEqual(
            api.log,
            ["create", "audio d1#0", "audio d1#1", "audio d1#2", "audio d1#1", "audio d1#2", "finish d1@2"]
        )
        XCTAssertEqual(recorded.events, ["done hello world"])
    }

    func testGivesUpAfterThreeResyncs() async {
        api.setAudioFallback(.fail(voiceRefusal(409, VoiceCodes.seqGap, expectedSeq: 0)))

        await uploadClosed(4)

        XCTAssertEqual(api.count("audio d1#0"), 4)
        XCTAssertEqual(recorded.failure, UploadFailure(code: VoiceCodes.seqGap, retryable: false, message: nil))
    }

    func testAGapAtASeqNeverSentIsFatal() async {
        api.enqueueAudio(.fail(voiceRefusal(409, VoiceCodes.seqGap, expectedSeq: 5)))

        await uploadClosed(4)

        XCTAssertEqual(recorded.failure, UploadFailure(code: VoiceCodes.seqGap, retryable: false, message: nil))
    }

    func testDeletesAnExpiredDictationOpensANewOneAndReUploadsFromSeqZero() async {
        api.enqueueAudio(.ok(()), .fail(voiceRefusal(404, VoiceCodes.dictationNotFound)))

        await uploadClosed(8)

        XCTAssertEqual(
            api.log,
            ["create", "audio d1#0", "audio d1#1", "remove d1", "create", "audio d2#0", "audio d2#1", "finish d2@1"]
        )
        XCTAssertEqual(api.payload("d2#0"), api.payload("d1#0"))
        XCTAssertEqual(recorded.events, ["done hello world"])
    }

    func testStopsRestartingAfterTwoAndFailsRetryably() async {
        api.setAudioFallback(.fail(voiceRefusal(404, VoiceCodes.dictationNotFound)))

        await uploadClosed(4)

        XCTAssertEqual(api.count("create"), 3)
        XCTAssertEqual(api.log.filter { $0.hasPrefix("remove") }, ["remove d1", "remove d2"])
        XCTAssertEqual(
            recorded.failure,
            UploadFailure(code: VoiceCodes.dictationNotFound, retryable: true, message: nil)
        )
    }

    func testStopsCaptureOnTooLongAndFinishesWithTheLastAcceptedChunk() async {
        api.enqueueAudio(.ok(()), .ok(()), .fail(voiceRefusal(409, VoiceCodes.tooLong)))
        buffer.append(voiceRamp(12))
        let upload = uploader()
        upload.start()
        await waitUntil { self.settled }

        XCTAssertEqual(recorded.events, ["stopCapture", "done hello world"])
        XCTAssertEqual(api.log, ["create", "audio d1#0", "audio d1#1", "audio d1#2", "finish d1@1"])
        upload.close()
    }

    func testLatchesTranscriptionFailedWithNoAutomaticReUploadAndRetryOpensANewDictation() async {
        api.enqueueAudio(
            .ok(()),
            .fail(voiceRefusal(503, VoiceCodes.transcriptionFailed, detailMessage: "model will not load"))
        )
        let upload = await uploadClosed(8)

        XCTAssertEqual(
            recorded.failure,
            UploadFailure(code: VoiceCodes.transcriptionFailed, retryable: true, message: "model will not load")
        )
        await idle()
        XCTAssertEqual(api.log, ["create", "audio d1#0", "audio d1#1"])

        XCTAssertTrue(upload.retry())
        await waitUntil { self.recorded.events.last == "done hello world" }

        XCTAssertEqual(
            Array(api.log.dropFirst(3)),
            ["remove d1", "create", "audio d2#0", "audio d2#1", "finish d2@1"]
        )
    }

    func testAsksFinishAgainWhileTranscribingAtLeastOneSecondApart() async {
        let transcribing = VoiceFinishResult(state: "transcribing", text: nil)
        api.enqueueFinish(.ok(transcribing), .ok(transcribing))

        await uploadClosed(4)

        XCTAssertEqual(api.count("finish d1@0"), 3)
        XCTAssertEqual(clock.sleeps.count, 2)
        for sleep in clock.sleeps {
            XCTAssertGreaterThan(sleep, 0.99)
            XCTAssertLessThanOrEqual(sleep, 1)
        }
        XCTAssertEqual(recorded.events, ["done hello world"])
    }

    func testGivesUpOnTranscribingAfterThe120sDeadlineRetryably() async {
        api.setFinishFallback(.ok(VoiceFinishResult(state: "transcribing", text: nil)))

        await uploadClosed(4)

        XCTAssertEqual(
            recorded.failure,
            UploadFailure(code: VoiceCodes.transcriptionTimeout, retryable: true, message: nil)
        )
        XCTAssertGreaterThan(clock.elapsed, 120)
        XCTAssertLessThanOrEqual(clock.elapsed, 122)
    }

    func testFailsAFinishThatKeepsGettingLost30sAfterRecordingStoppedNotAtTheDeadline() async {
        api.setFinishFallback(.fail(voiceNetworkError()))

        await uploadClosed(4)

        XCTAssertEqual(recorded.failure, UploadFailure(code: VoiceCodes.network, retryable: true, message: "offline"))
        XCTAssertGreaterThan(clock.elapsed, 30)
        XCTAssertLessThanOrEqual(clock.elapsed, 34)
    }

    func testAnyFinishAnswerRestartsThe30sStallWindow() async {
        let transcribing = VoiceFinishResult(state: "transcribing", text: nil)
        api.enqueueFinish(.fail(voiceNetworkError()))
        for _ in 0..<31 { api.enqueueFinish(.ok(transcribing)) }
        api.enqueueFinish(.fail(voiceNetworkError()))

        await uploadClosed(4)

        XCTAssertGreaterThan(clock.elapsed, 30)
        XCTAssertEqual(recorded.events.last, "done hello world")
    }

    func testRefusalsAreRetryableOnlyWhereARetryCanWork() async {
        let cases: [(Int, String, Bool)] = [
            (409, VoiceCodes.busy, true),
            (409, VoiceCodes.notReady, false),
            (422, VoiceCodes.badAudio, false),
            (413, VoiceCodes.chunkTooLarge, false),
            (503, "something_new", true),
            (400, "something_new", false),
        ]
        for (status, code, retryable) in cases {
            api = FakeVoiceApi()
            recorded = RecordingUploaderCallbacks()
            buffer = PcmBuffer(capacitySamples: 8)
            api.enqueueAudio(.fail(voiceRefusal(status, code)))

            await uploadClosed(4)

            XCTAssertEqual(recorded.failure?.code, code, "\(status) \(code)")
            XCTAssertEqual(recorded.failure?.retryable, retryable, "\(status) \(code)")
            XCTAssertEqual(api.log, ["create", "audio d1#0"], "\(status) \(code)")
        }
    }

    func testRefusesAndDeletesADictationOpenedAtAnotherSampleRate() async {
        api.configure(sampleRate: 48_000)

        await uploadClosed(4)

        XCTAssertEqual(api.log, ["create", "remove d1"])
        XCTAssertEqual(recorded.failure, UploadFailure(code: VoiceCodes.generic, retryable: false, message: nil))
    }

    func testCancelNeverAbortsACreateAndDeletesTheIdItReturns() async {
        let create = HeldReply<VoiceDictationOpened>()
        api.enqueueCreate(.hold(create))
        buffer.append(voiceRamp(4))
        let upload = uploader()
        upload.start()
        await waitUntil { create.entered }

        upload.cancel()
        await idle()
        XCTAssertFalse(create.aborted)

        create.resolve(api.opened("d7"))
        await waitUntil { self.api.log.count == 2 }
        await idle()

        XCTAssertEqual(api.log, ["create", "remove d7"])
        XCTAssertEqual(recorded.events, [])
    }

    func testCancelAbortsTheChunkInFlightDeletesTheDictationAndReportsNothing() async {
        let chunk = HeldReply<Void>()
        api.enqueueAudio(.hold(chunk))
        buffer.append(voiceRamp(4))
        let upload = uploader()
        upload.start()
        await waitUntil { chunk.entered }

        upload.cancel()
        await waitUntil { chunk.aborted }
        await idle()

        XCTAssertTrue(chunk.aborted)
        XCTAssertEqual(api.log, ["create", "audio d1#0", "remove d1"])
        XCTAssertEqual(recorded.events, [])
        XCTAssertFalse(upload.retry())
    }
}
