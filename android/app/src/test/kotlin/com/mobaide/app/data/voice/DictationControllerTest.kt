package com.mobaide.app.data.voice

import com.mobaide.app.testing.FakeRecorder
import com.mobaide.app.testing.FakeUploaderClock
import com.mobaide.app.testing.FakeVoiceApi
import com.mobaide.app.testing.Reply
import com.mobaide.app.testing.refusal
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [DictationController]'s transitions with a fake microphone and the scripted
 * agent, on virtual time: Idle → Starting → Recording → Transcribing →
 * Result | Empty | Failed, plus cancel, retry, dismiss and dispose.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class DictationControllerTest {

    private class Harness(val test: TestScope, val scope: CoroutineScope) {
        val api = FakeVoiceApi(test.testScheduler).apply { maxChunkBytes = 32_000 }
        val recorder = FakeRecorder()
        val states = mutableListOf<DictationState>()

        fun controller(maxSeconds: Int = 120) = DictationController(
            api = api,
            createRecorder = { recorder },
            sampleRate = 16_000,
            maxSeconds = maxSeconds,
            scope = scope,
            clock = FakeUploaderClock(test.testScheduler),
        )

        fun settle() = test.advanceUntilIdle()

        fun recording(maxSeconds: Int = 120): DictationController = controller(maxSeconds).also {
            it.start()
            settle()
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
}
