package com.mobaide.app.data.voice

import com.mobaide.app.testing.FakeRecorder
import com.mobaide.app.testing.FakeUploaderClock
import com.mobaide.app.testing.FakeVoiceApi
import com.mobaide.app.testing.Reply
import com.mobaide.app.testing.refusal
import com.mobaide.app.testing.networkError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [DictationController]'s transitions with a fake microphone and the scripted
 * agent, on virtual time: Idle → Starting → Recording → Transcribing →
 * (Cleaning →) Result | Empty | Failed, plus cancel, retry, dismiss and dispose.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class DictationControllerTest {

    private class Harness(val test: TestScope, val scope: CoroutineScope) {
        val api = FakeVoiceApi(test.testScheduler).apply { maxChunkBytes = 32_000 }
        val recorder = FakeRecorder()
        val states = mutableListOf<DictationState>()

        fun controller(
            maxSeconds: Int = 120,
            cleaner: () -> TranscriptCleaner? = { null },
        ) = DictationController(
            api = api,
            createRecorder = { recorder },
            sampleRate = 16_000,
            maxSeconds = maxSeconds,
            scope = scope,
            clock = FakeUploaderClock(test.testScheduler),
            cleaner = cleaner,
        )

        fun settle() = test.advanceUntilIdle()

        fun recording(
            maxSeconds: Int = 120,
            cleaner: () -> TranscriptCleaner? = { null },
        ): DictationController = controller(maxSeconds, cleaner).also {
            it.start()
            settle()
        }

        /**
         * A recorded second, stopped and transcribed as "hello world", with
         * [cleaner] asked to clean it up. Virtual time stands still, so a held
         * cleanup is still running.
         */
        fun cleaning(cleaner: FakeCleaner): DictationController = recording(cleaner = { cleaner }).also {
            recorder.speak(1)
            settle()
            it.stop()
            test.testScheduler.runCurrent()
        }
    }

    /** The session's AI agent on the agent's computer, answering [reply]; every transcript is kept. */
    private class FakeCleaner : TranscriptCleaner {
        val calls = mutableListOf<String>()
        var reply: Reply<String> = Reply.Ok("Hello, world.")

        override suspend fun clean(text: String): String {
            calls += text
            return when (val reply = reply) {
                is Reply.Ok -> reply.value
                is Reply.Fail -> throw reply.error
                is Reply.Hold -> {
                    reply.entered = true
                    try {
                        reply.answer.await()
                    } catch (e: CancellationException) {
                        reply.aborted = true
                        throw e
                    }
                }
            }
        }
    }

    private fun controllerTest(body: Harness.() -> Unit) = runTest {
        val scope = CoroutineScope(StandardTestDispatcher(testScheduler) + SupervisorJob())
        try {
            Harness(this, scope).body()
        } finally {
            scope.cancel()
        }
    }

    @Test
    fun records_transcribesOnStop_andHandsTheTextBackAsAResult() = controllerTest {
        val dictation = recording()
        recorder.sink?.level(0.4f)
        recorder.speak(1)
        settle()

        assertEquals(
            DictationState.Recording(elapsedMs = 1_000, level = 0.4f, remainingSeconds = null, lagging = false),
            dictation.state.value,
        )

        dictation.toggle()
        assertEquals(DictationState.Transcribing(interruptedBy = null, lagging = false), dictation.state.value)
        settle()

        assertEquals(DictationState.Result("hello world"), dictation.state.value)
        assertEquals(16_000, recorder.sampleRate)
        assertEquals(1, recorder.stops)
        assertEquals(listOf("create", "audio d1#0", "finish d1@0"), api.log)
    }

    @Test
    fun anEmptyTranscript_readsAsEmpty() = controllerTest {
        api.text = ""
        val dictation = recording()
        recorder.speak(1)
        settle()

        dictation.stop()
        settle()

        assertEquals(DictationState.Empty, dictation.state.value)
    }

    @Test
    fun countsDownTheLast15sFromTheSamplesRecorded() = controllerTest {
        val dictation = recording(maxSeconds = 20)

        recorder.speak(4)
        settle()
        assertEquals(null, (dictation.state.value as DictationState.Recording).remainingSeconds)

        recorder.speak(2)
        settle()
        val state = dictation.state.value as DictationState.Recording
        assertEquals(6_000L, state.elapsedMs)
        assertEquals(14, state.remainingSeconds)
    }

    @Test
    fun stopsByItselfAtTheLimit_andStillTranscribes() = controllerTest {
        val finish = Reply.Hold<VoiceFinishResult>()
        api.finishReplies += finish
        val dictation = recording(maxSeconds = 2)

        recorder.speak(2)
        test.testScheduler.runCurrent()

        assertEquals(DictationState.Transcribing(InterruptReason.Limit, lagging = false), dictation.state.value)
        finish.answer.complete(VoiceFinishResult("done", "hello world"))
        test.testScheduler.runCurrent()
        assertEquals(DictationState.Result("hello world"), dictation.state.value)
    }

    @Test
    fun anInterruption_transcribesWhatWasRecorded() = controllerTest {
        val finish = Reply.Hold<VoiceFinishResult>()
        api.finishReplies += finish
        val dictation = recording()
        recorder.speak(1)
        test.testScheduler.runCurrent()

        recorder.interrupt(InterruptReason.Call)
        test.testScheduler.runCurrent()

        assertEquals(DictationState.Transcribing(InterruptReason.Call, lagging = false), dictation.state.value)
        assertEquals(1, recorder.stops)
        finish.answer.complete(VoiceFinishResult("done", "hello world"))
        test.testScheduler.runCurrent()
        assertEquals(DictationState.Result("hello world"), dictation.state.value)
    }

    @Test
    fun aRefusedMicrophone_failsWithoutAudio_andDeletesTheOpenedDictation() = controllerTest {
        recorder.startFailure = VoiceRecorderException(VoiceCodes.MIC_PERMISSION_DENIED)
        val dictation = controller()

        dictation.start()
        settle()

        assertEquals(
            DictationState.Failed(
                code = VoiceCodes.MIC_PERMISSION_DENIED,
                retryable = false,
                message = VoiceCodes.MIC_PERMISSION_DENIED,
                hasAudio = false,
            ),
            dictation.state.value,
        )
        assertEquals(listOf("create", "remove d1"), api.log)
    }

    @Test
    fun anUploadFailure_keepsTheAudio_soRetryNeedsNoNewRecording() = controllerTest {
        api.finishReplies += Reply.Fail(refusal(503, VoiceCodes.TRANSCRIPTION_FAILED))
        val dictation = recording()
        recorder.speak(1)
        settle()
        dictation.stop()
        settle()

        val failed = dictation.state.value as DictationState.Failed
        assertEquals(VoiceCodes.TRANSCRIPTION_FAILED, failed.code)
        assertTrue(failed.retryable)
        assertTrue(failed.hasAudio)

        dictation.retry()
        assertEquals(DictationState.Transcribing(null, lagging = false), dictation.state.value)
        settle()

        assertEquals(DictationState.Result("hello world"), dictation.state.value)
        assertEquals(listOf("remove d1", "create", "audio d2#0", "finish d2@0"), api.log.drop(3))
    }

    @Test
    fun retry_isIgnoredForAFailureThatCannotSucceedAgain() = controllerTest {
        api.audioReplies += Reply.Fail(refusal(422, VoiceCodes.BAD_AUDIO))
        val dictation = recording()
        recorder.speak(1)
        settle()
        assertEquals(VoiceCodes.BAD_AUDIO, (dictation.state.value as DictationState.Failed).code)

        dictation.retry()
        settle()

        assertTrue(dictation.state.value is DictationState.Failed)
        assertEquals(1, api.log.count { it == "create" })
    }

    @Test
    fun cancel_returnsToIdle_deletesTheDictation_andTranscribesNothing() = controllerTest {
        val dictation = recording()
        recorder.speak(1)
        settle()

        dictation.cancel()
        settle()

        assertEquals(DictationState.Idle, dictation.state.value)
        assertEquals(1, recorder.stops)
        assertEquals(listOf("create", "audio d1#0", "remove d1"), api.log)
    }

    @Test
    fun toggleWhileTheMicrophoneIsStillOpening_abandonsTheStart() = controllerTest {
        val gate = CompletableDeferred<Unit>()
        recorder.startGate = gate
        val dictation = controller()
        dictation.start()
        settle()
        assertEquals(DictationState.Starting, dictation.state.value)

        dictation.toggle()
        gate.complete(Unit)
        settle()

        assertEquals(DictationState.Idle, dictation.state.value)
        assertTrue(recorder.stops >= 1)
        assertEquals(listOf("create", "remove d1"), api.log)
    }

    @Test
    fun dismiss_leavesAResult() = controllerTest {
        val dictation = recording()
        recorder.speak(1)
        settle()
        dictation.stop()
        settle()

        dictation.dismiss()

        assertEquals(DictationState.Idle, dictation.state.value)
    }

    @Test
    fun dispose_cancels_andStartsNothingAfterwards() = controllerTest {
        val dictation = recording()
        recorder.speak(1)
        settle()

        dictation.dispose()
        dictation.start()
        settle()

        assertEquals(DictationState.Idle, dictation.state.value)
        assertEquals(1, api.log.count { it == "create" })
        assertEquals("remove d1", api.log.last())
    }

    @Test
    fun dispose_afterTheScopeIsCancelled_stillClosesTheMicrophone() = controllerTest {
        val dictation = recording()
        recorder.speak(1)
        settle()

        scope.cancel()
        dictation.dispose()
        settle()

        assertEquals(1, recorder.stops)
    }

    @Test
    fun theScopeEndingWhileTheMicrophoneIsOpening_stillClosesIt() = controllerTest {
        recorder.startGate = CompletableDeferred()
        val dictation = controller()
        dictation.start()
        settle()
        assertEquals(DictationState.Starting, dictation.state.value)

        scope.cancel()
        settle()

        assertEquals(1, recorder.stops)
    }

    @Test
    fun cleanup_cleansTheTranscriptUpBeforeHandingItBack() = controllerTest {
        val held = Reply.Hold<String>()
        val cleaner = FakeCleaner().apply { reply = held }
        val dictation = cleaning(cleaner)

        assertEquals(DictationState.Cleaning("hello world"), dictation.state.value)
        held.answer.complete("Hello, world.")
        settle()

        assertEquals(DictationState.Result("Hello, world."), dictation.state.value)
        assertEquals(listOf("hello world"), cleaner.calls)
    }

    @Test
    fun cleanup_thatComesBackUnchangedOrEmpty_handsBackAPlainResult() = controllerTest {
        for (answer in listOf("hello world", "  hello world\n", "  ")) {
            val cleaner = FakeCleaner().apply { reply = Reply.Ok(answer) }
            val dictation = cleaning(cleaner)
            settle()

            assertEquals(answer, DictationState.Result("hello world"), dictation.state.value)
            dictation.dismiss()
        }
    }

    @Test
    fun cleanup_thatFails_handsOverTheTranscriptAsHeard_sayingWhy() = controllerTest {
        val failures = listOf(
            refusal(503, VoiceCodes.CLEANUP_OUTDATED, detailMessage = "Run claude update.") to
                CleanupError(VoiceCodes.CLEANUP_OUTDATED, "Run claude update."),
            refusal(502, null) to CleanupError(VoiceCodes.GENERIC, "HTTP 502"),
            networkError() to CleanupError(VoiceCodes.NETWORK, "offline"),
            IllegalStateException("boom") to CleanupError(VoiceCodes.GENERIC, "boom"),
        )
        for ((error, expected) in failures) {
            val cleaner = FakeCleaner().apply { reply = Reply.Fail(error) }
            val dictation = cleaning(cleaner)
            settle()

            assertEquals(DictationState.Result("hello world", cleanupError = expected), dictation.state.value)
            dictation.dismiss()
        }
    }

    @Test
    fun cleanup_waitsPastTheRelayTimeout_thenGivesUpAt35sWithTheTranscriptAsHeard() = controllerTest {
        val held = Reply.Hold<String>()
        val dictation = cleaning(FakeCleaner().apply { reply = held })

        test.testScheduler.advanceTimeBy(30_000)
        test.testScheduler.runCurrent()
        assertEquals(DictationState.Cleaning("hello world"), dictation.state.value)

        test.testScheduler.advanceTimeBy(5_000)
        test.testScheduler.runCurrent()

        assertTrue(held.aborted)
        assertEquals(
            DictationState.Result("hello world", CleanupError(VoiceCodes.CLEANUP_TIMEOUT, null)),
            dictation.state.value,
        )
    }

    @Test
    fun cancel_whileCleaningUp_returnsToIdle_andAbortsTheRequest() = controllerTest {
        val held = Reply.Hold<String>()
        val dictation = cleaning(FakeCleaner().apply { reply = held })

        dictation.cancel()
        test.testScheduler.runCurrent()

        assertTrue(held.aborted)
        assertEquals(DictationState.Idle, dictation.state.value)
    }

    @Test
    fun theMic_isIgnoredWhileCleaningUp() = controllerTest {
        val dictation = cleaning(FakeCleaner().apply { reply = Reply.Hold<String>() })

        dictation.toggle()
        dictation.start()
        test.testScheduler.runCurrent()

        assertEquals(DictationState.Cleaning("hello world"), dictation.state.value)
        assertEquals(1, api.log.count { it == "create" })
    }

    @Test
    fun anAbandonedCleanupAnswer_neverReachesTheNextDictation() = controllerTest {
        // A reply already on its way when the user cancels ignores the abort.
        val answers = mutableListOf<CompletableDeferred<String>>()
        val deaf = TranscriptCleaner {
            val answer = CompletableDeferred<String>().also { answers += it }
            withContext(NonCancellable) { answer.await() }
        }
        val dictation = recording(cleaner = { deaf })
        recorder.speak(1)
        settle()
        dictation.stop()
        test.testScheduler.runCurrent()
        dictation.cancel()
        dictation.start()
        test.testScheduler.runCurrent()
        recorder.speak(1)
        test.testScheduler.runCurrent()
        dictation.stop()
        test.testScheduler.runCurrent()

        answers[0].complete("Stale.")
        test.testScheduler.runCurrent()
        assertEquals(DictationState.Cleaning("hello world"), dictation.state.value)

        answers[1].complete("Fresh.")
        test.testScheduler.runCurrent()
        assertEquals(DictationState.Result("Fresh."), dictation.state.value)
    }

    @Test
    fun theCleanupSetting_isReadWhenTheTranscriptArrives_notWhenRecordingStarts() = controllerTest {
        val cleaner = FakeCleaner()
        var cleanUp = false
        val dictation = recording(cleaner = { if (cleanUp) cleaner else null })
        recorder.speak(1)
        settle()

        cleanUp = true
        dictation.stop()
        settle()

        assertEquals(DictationState.Result("Hello, world."), dictation.state.value)
    }

    @Test
    fun nothingHeard_sendsNothingToCleanUp() = controllerTest {
        api.text = ""
        val cleaner = FakeCleaner()
        val dictation = cleaning(cleaner)
        settle()

        assertEquals(DictationState.Empty, dictation.state.value)
        assertEquals(emptyList<String>(), cleaner.calls)
    }
}
