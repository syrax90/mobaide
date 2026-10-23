package com.mobaide.app.data.voice

import com.mobaide.app.testing.FakeUploaderClock
import com.mobaide.app.testing.FakeVoiceApi
import com.mobaide.app.testing.RecordingListener
import com.mobaide.app.testing.Reply
import com.mobaide.app.testing.networkError
import com.mobaide.app.testing.ramp
import com.mobaide.app.testing.refusal
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * [DictationUploader] against a scripted agent on virtual time. The fake agent
 * answers `max_chunk_bytes: 8`, so a chunk is 4 samples. Held requests are
 * driven with `runCurrent()` only: `advanceUntilIdle()` would let the 15 s
 * request timeout fire on them.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class DictationUploaderTest {

    private class Harness(val test: TestScope, val scope: CoroutineScope) {
        val api = FakeVoiceApi(test.testScheduler)
        val clock = FakeUploaderClock(test.testScheduler)
        val listener = RecordingListener()
        val buffer = PcmBuffer(1_000)

        fun uploader() = DictationUploader(api, buffer, 16_000, listener, scope, clock)

        /** Start over [samples] already recorded, stop recording, run to the end. */
        fun uploadClosed(samples: Int): DictationUploader {
            buffer.append(ramp(samples))
            val upload = uploader()
            upload.start()
            upload.close()
            test.advanceUntilIdle()
            return upload
        }
    }

    private fun uploaderTest(body: Harness.() -> Unit) = runTest {
        val scope = CoroutineScope(StandardTestDispatcher(testScheduler) + SupervisorJob())
        try {
            Harness(this, scope).body()
        } finally {
            scope.cancel()
        }
    }

    @Test
    fun uploadsFullChunksWhileRecording_thenTheTail_thenFinishesWithTheLastSeq() = uploaderTest {
        buffer.append(ramp(10))
        val upload = uploader()
        upload.start()
        test.advanceUntilIdle()

        assertEquals(listOf("create", "audio d1#0", "audio d1#1"), api.log)

        upload.close()
        test.advanceUntilIdle()

        assertEquals(listOf("create", "audio d1#0", "audio d1#1", "audio d1#2", "finish d1@2"), api.log)
        assertEquals(buffer.payload(2, 4), api.payloads["d1#2"])
        assertEquals(listOf("done hello world"), listener.events)
    }

    @Test
    fun keepsOneAudioRequestInFlight_andFinishesOnlyAfterTheLastIsAnswered() = uploaderTest {
        val first = Reply.Hold<Unit>()
        api.audioReplies += first
        buffer.append(ramp(4))
        val upload = uploader()
        upload.start()
        test.runCurrent()

        buffer.append(ramp(6, from = 100))
        upload.onAudio()
        upload.onAudio()
        upload.close()
        test.runCurrent()

        assertEquals(listOf("create", "audio d1#0"), api.log)

        first.answer.complete(Unit)
        test.runCurrent()

        assertEquals(listOf("create", "audio d1#0", "audio d1#1", "audio d1#2", "finish d1@2"), api.log)
        assertEquals(listOf("done hello world"), listener.events)
    }

    @Test
    fun trimsTheTranscript() = uploaderTest {
        api.text = "  spaced out \n"

        uploadClosed(3)

        assertEquals(listOf("done spaced out"), listener.events)
    }

    @Test
    fun retriesALostRequestWithBackoffAndJitter_andReportsLagging() = uploaderTest {
        clock.randomValue = 1.0
        repeat(5) { api.audioReplies += Reply.Fail(networkError()) }

        uploadClosed(4)

        val sends = api.timesOf("audio d1#0")
        assertEquals(listOf(600L, 1_200L, 2_400L, 4_800L, 4_800L), sends.zipWithNext { a, b -> b - a })
        assertEquals(listOf("lagging true", "lagging false", "done hello world"), listener.events)
    }

    @Test
    fun retriesAnIoError_anUncoded5xx_andA503Busy_likeALostRequest() = uploaderTest {
        api.audioReplies += Reply.Fail(IOException("reset"))
        api.audioReplies += Reply.Fail(refusal(502, null))
        api.audioReplies += Reply.Fail(refusal(503, VoiceCodes.BUSY))

        uploadClosed(4)

        assertEquals(4, api.timesOf("audio d1#0").size)
        assertEquals("done hello world", listener.events.last())
    }

    @Test
    fun neverGivesUpOnAStalledUploadWhileStillRecording() = uploaderTest {
        repeat(30) { api.audioReplies += Reply.Fail(networkError()) }
        buffer.append(ramp(4))
        val upload = uploader()
        upload.start()
        test.advanceUntilIdle()

        assertTrue(test.testScheduler.currentTime > 60_000)
        assertEquals(listOf("lagging true", "lagging false"), listener.events)
        assertEquals("audio d1#0", api.log.last())
        upload.cancel()
    }

    @Test
    fun failsAStalledUpload30sAfterRecordingStopped_retryably() = uploaderTest {
        api.audioFallback = { Reply.Fail(networkError()) }

        uploadClosed(4)

        assertEquals(UploadFailure(VoiceCodes.NETWORK, retryable = true, message = "offline"), listener.failure)
        assertTrue(test.testScheduler.currentTime in 30_001L..34_000L)
    }

    @Test
    fun retryContinuesALiveDictationWhereItStopped() = uploaderTest {
        api.audioReplies += Reply.Ok(Unit)
        api.audioFallback = { Reply.Fail(networkError()) }
        val upload = uploadClosed(8)
        assertEquals(VoiceCodes.NETWORK, listener.failure?.code)
        api.audioFallback = { Reply.Ok(Unit) }

        assertTrue(upload.retry())
        test.advanceUntilIdle()

        assertEquals(
            listOf("create", "audio d1#0", "finish d1@1"),
            api.log.filterNot { it == "audio d1#1" },
        )
        assertEquals("done hello world", listener.events.last())
    }

    @Test
    fun resendsFromTheExpectedSeqAfterASeqGap() = uploaderTest {
        api.audioReplies += Reply.Ok(Unit)
        api.audioReplies += Reply.Ok(Unit)
        api.audioReplies += Reply.Fail(refusal(409, VoiceCodes.SEQ_GAP, expectedSeq = 1))

        uploadClosed(12)

        assertEquals(
            listOf("create", "audio d1#0", "audio d1#1", "audio d1#2", "audio d1#1", "audio d1#2", "finish d1@2"),
            api.log,
        )
        assertEquals(listOf("done hello world"), listener.events)
    }

    @Test
    fun resendsFromTheExpectedSeqWhenFinishReportsAGap() = uploaderTest {
        api.finishReplies += Reply.Fail(refusal(409, VoiceCodes.SEQ_GAP, expectedSeq = 1))

        uploadClosed(8)

        assertEquals(
            listOf("create", "audio d1#0", "audio d1#1", "finish d1@1", "audio d1#1", "finish d1@1"),
            api.log,
        )
    }

    @Test
    fun givesUpAfterThreeResyncs() = uploaderTest {
        api.audioFallback = { Reply.Fail(refusal(409, VoiceCodes.SEQ_GAP, expectedSeq = 0)) }

        uploadClosed(4)

        assertEquals(4, api.timesOf("audio d1#0").size)
        assertEquals(UploadFailure(VoiceCodes.SEQ_GAP, retryable = false, message = null), listener.failure)
    }

    @Test
    fun aGapAtASeqNeverSent_isFatal() = uploaderTest {
        api.audioReplies += Reply.Fail(refusal(409, VoiceCodes.SEQ_GAP, expectedSeq = 5))

        uploadClosed(4)

        assertEquals(UploadFailure(VoiceCodes.SEQ_GAP, retryable = false, message = null), listener.failure)
    }

    @Test
    fun deletesAnExpiredDictation_opensANewOne_andReUploadsFromSeqZero() = uploaderTest {
        api.audioReplies += Reply.Ok(Unit)
        api.audioReplies += Reply.Fail(refusal(404, VoiceCodes.DICTATION_NOT_FOUND))

        uploadClosed(8)

        assertEquals(
            listOf(
                "create", "audio d1#0", "audio d1#1", "remove d1",
                "create", "audio d2#0", "audio d2#1", "finish d2@1",
            ),
            api.log,
        )
        assertEquals(api.payloads["d1#0"], api.payloads["d2#0"])
        assertEquals(listOf("done hello world"), listener.events)
    }

    @Test
    fun stopsRestartingAfterTwo_andFailsRetryably() = uploaderTest {
        api.audioFallback = { Reply.Fail(refusal(404, VoiceCodes.DICTATION_NOT_FOUND)) }

        uploadClosed(4)

        assertEquals(3, api.log.count { it == "create" })
        assertEquals(listOf("remove d1", "remove d2"), api.log.filter { it.startsWith("remove") })
        assertEquals(
            UploadFailure(VoiceCodes.DICTATION_NOT_FOUND, retryable = true, message = null),
            listener.failure,
        )
    }

    @Test
    fun stopsCaptureOnTooLong_andFinishesWithTheLastAcceptedChunk() = uploaderTest {
        api.audioReplies += Reply.Ok(Unit)
        api.audioReplies += Reply.Ok(Unit)
        api.audioReplies += Reply.Fail(refusal(409, VoiceCodes.TOO_LONG))
        buffer.append(ramp(12))
        val upload = uploader()
        upload.start()
        test.advanceUntilIdle()

        assertEquals(listOf("stopCapture", "done hello world"), listener.events)
        assertEquals(listOf("create", "audio d1#0", "audio d1#1", "audio d1#2", "finish d1@1"), api.log)
        upload.close()
    }

    @Test
    fun reUploadsFromSeqZero_whenTheDictationExpiresAfterTooLong() = uploaderTest {
        repeat(2) {
            api.audioReplies += Reply.Ok(Unit)
            api.audioReplies += Reply.Ok(Unit)
            api.audioReplies += Reply.Fail(refusal(409, VoiceCodes.TOO_LONG))
        }
        api.finishReplies += Reply.Fail(refusal(404, VoiceCodes.DICTATION_NOT_FOUND))
        buffer.append(ramp(12))
        val upload = uploader()
        upload.start()
        test.advanceUntilIdle()

        assertEquals(
            listOf(
                "create", "audio d1#0", "audio d1#1", "audio d1#2", "finish d1@1", "remove d1",
                "create", "audio d2#0", "audio d2#1", "audio d2#2", "finish d2@1",
            ),
            api.log,
        )
        assertEquals("done hello world", listener.events.last())
        upload.close()
    }

    @Test
    fun reUploadsFromSeqZero_whenRetryOpensANewDictationAfterTooLong() = uploaderTest {
        api.audioReplies += Reply.Ok(Unit)
        api.audioReplies += Reply.Ok(Unit)
        api.audioReplies += Reply.Fail(refusal(409, VoiceCodes.TOO_LONG))
        api.finishReplies += Reply.Fail(refusal(503, VoiceCodes.TRANSCRIPTION_FAILED))
        buffer.append(ramp(12))
        val upload = uploader()
        upload.start()
        test.advanceUntilIdle()
        assertEquals(VoiceCodes.TRANSCRIPTION_FAILED, listener.failure?.code)

        assertTrue(upload.retry())
        test.advanceUntilIdle()

        assertEquals(
            listOf("remove d1", "create", "audio d2#0", "audio d2#1", "audio d2#2", "finish d2@2"),
            api.log.drop(5),
        )
        assertEquals("done hello world", listener.events.last())
    }

    @Test
    fun latchesTranscriptionFailed_noAutomaticReUpload_andRetryOpensANewDictation() = uploaderTest {
        api.audioReplies += Reply.Ok(Unit)
        api.audioReplies += Reply.Fail(
            refusal(503, VoiceCodes.TRANSCRIPTION_FAILED, detailMessage = "model will not load"),
        )
        val upload = uploadClosed(8)

        assertEquals(
            UploadFailure(VoiceCodes.TRANSCRIPTION_FAILED, retryable = true, message = "model will not load"),
            listener.failure,
        )
        assertEquals(listOf("create", "audio d1#0", "audio d1#1"), api.log)

        assertTrue(upload.retry())
        test.advanceUntilIdle()

        assertEquals(
            listOf("remove d1", "create", "audio d2#0", "audio d2#1", "finish d2@1"),
            api.log.drop(3),
        )
        assertEquals("done hello world", listener.events.last())
    }

    @Test
    fun asksFinishAgainWhileTranscribing_atLeastOneSecondApart() = uploaderTest {
        api.finishReplies += Reply.Ok(VoiceFinishResult("transcribing", null))
        api.finishReplies += Reply.Ok(VoiceFinishResult("transcribing", null))

        uploadClosed(4)

        val asks = api.timesOf("finish d1@0")
        assertEquals(listOf(1_000L, 1_000L), asks.zipWithNext { a, b -> b - a })
        assertEquals(listOf("done hello world"), listener.events)
    }

    @Test
    fun givesUpOnTranscribingAfterThe120sDeadline_retryably() = uploaderTest {
        api.finishFallback = { Reply.Ok(VoiceFinishResult("transcribing", null)) }

        uploadClosed(4)

        assertEquals(
            UploadFailure(VoiceCodes.TRANSCRIPTION_TIMEOUT, retryable = true, message = null),
            listener.failure,
        )
        assertTrue(test.testScheduler.currentTime in 120_001L..122_000L)
    }

    @Test
    fun failsAFinishThatKeepsGettingLost30sAfterRecordingStopped_notAtTheDeadline() = uploaderTest {
        api.finishFallback = { Reply.Fail(networkError()) }

        uploadClosed(4)

        assertEquals(UploadFailure(VoiceCodes.NETWORK, retryable = true, message = "offline"), listener.failure)
        assertTrue(test.testScheduler.currentTime in 30_001L..34_000L)
    }

    @Test
    fun anyFinishAnswerRestartsThe30sStallWindow() = uploaderTest {
        api.finishReplies += Reply.Fail(networkError())
        repeat(31) { api.finishReplies += Reply.Ok(VoiceFinishResult("transcribing", null)) }
        api.finishReplies += Reply.Fail(networkError())

        uploadClosed(4)

        assertTrue(test.testScheduler.currentTime > 30_000)
        assertEquals("done hello world", listener.events.last())
    }

    @Test
    fun refusals_areRetryableOnlyWhereARetryCanWork() = uploaderTest {
        val cases = listOf(
            Triple(409, VoiceCodes.BUSY, true),
            Triple(409, VoiceCodes.NOT_READY, false),
            Triple(422, VoiceCodes.BAD_AUDIO, false),
            Triple(413, VoiceCodes.CHUNK_TOO_LARGE, false),
            Triple(503, "something_new", true),
            Triple(400, "something_new", false),
        )
        for ((status, code, retryable) in cases) {
            val api = FakeVoiceApi(test.testScheduler)
            val listener = RecordingListener()
            val buffer = PcmBuffer(8).apply { append(ramp(4)) }
            api.audioReplies += Reply.Fail(refusal(status, code))
            val upload = DictationUploader(api, buffer, 16_000, listener, scope, clock)
            upload.start()
            upload.close()
            test.advanceUntilIdle()

            assertEquals("$status $code", code, listener.failure?.code)
            assertEquals("$status $code", retryable, listener.failure?.retryable)
            assertEquals("$status $code", listOf("create", "audio d1#0"), api.log)
        }
    }

    @Test
    fun refusesAndDeletesADictationOpenedAtAnotherSampleRate() = uploaderTest {
        api.sampleRate = 48_000

        uploadClosed(4)

        assertEquals(listOf("create", "remove d1"), api.log)
        assertEquals(UploadFailure(VoiceCodes.GENERIC, retryable = false, message = null), listener.failure)
    }

    @Test
    fun cancelNeverAbortsACreate_andDeletesTheIdItReturns() = uploaderTest {
        val create = Reply.Hold<VoiceDictationOpened>()
        api.createReplies += create
        buffer.append(ramp(4))
        val upload = uploader()
        upload.start()
        test.runCurrent()

        upload.cancel()
        test.runCurrent()
        assertFalse(create.aborted)

        create.answer.complete(api.opened("d7"))
        test.runCurrent()

        assertEquals(listOf("create", "remove d7"), api.log)
        assertEquals(emptyList<String>(), listener.events)
    }

    @Test
    fun cancelAbortsTheChunkInFlight_deletesTheDictation_andReportsNothing() = uploaderTest {
        val chunk = Reply.Hold<Unit>()
        api.audioReplies += chunk
        buffer.append(ramp(4))
        val upload = uploader()
        upload.start()
        test.runCurrent()
        assertTrue(chunk.entered)

        upload.cancel()
        test.runCurrent()

        assertTrue(chunk.aborted)
        assertEquals(listOf("create", "audio d1#0", "remove d1"), api.log)
        assertEquals(emptyList<String>(), listener.events)
        assertFalse(upload.retry())
    }
}
