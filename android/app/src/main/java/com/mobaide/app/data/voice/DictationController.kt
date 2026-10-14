package com.mobaide.app.data.voice

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.ceil

sealed interface DictationState {
    data object Idle : DictationState
    data object Starting : DictationState
    data class Recording(
        val elapsedMs: Long,
        /** 0…1, for the level meter. */
        val level: Float,
        /** Seconds left before the agent's limit, once 15 or fewer remain. */
        val remainingSeconds: Int?,
        /** Uploads are behind (network trouble); recording carries on. */
        val lagging: Boolean,
    ) : DictationState

    data class Transcribing(val interruptedBy: InterruptReason?, val lagging: Boolean) : DictationState
    data class Result(val text: String) : DictationState
    data object Empty : DictationState
    data class Failed(
        val code: String,
        val retryable: Boolean,
        /** The agent's raw message, shown only behind "Details". */
        val message: String?,
        /** Audio is still buffered, so Retry needs no new recording. */
        val hasAudio: Boolean,
    ) : DictationState
}

/**
 * One dictation at a time, as the UI sees it: microphone → buffer → uploader
 * → transcript. Confined to [scope]'s thread (main in the app); recorder
 * callbacks are hopped onto it. Elapsed time is counted in samples, so the
 * countdown and the agent's `max_seconds` agree exactly.
 */
class DictationController(
    private val api: VoiceApi,
    private val createRecorder: () -> VoiceRecorder,
    private val sampleRate: Int,
    private val maxSeconds: Int,
    private val scope: CoroutineScope,
    private val clock: DictationUploader.Clock = DictationUploader.Clock.System,
) {
    private val _state = MutableStateFlow<DictationState>(DictationState.Idle)
    val state: StateFlow<DictationState> = _state.asStateFlow()

    /** Bumped by every new dictation and every cancel; stale callbacks compare it. */
    private var generation = 0
    private var recorder: VoiceRecorder? = null
    private var buffer: PcmBuffer? = null
    private var uploader: DictationUploader? = null
    private var level = 0f
    private var lagging = false
    private var disposed = false

    /** The mic button: start, stop, or abandon a start that is still opening. */
    fun toggle() {
        when (_state.value) {
            is DictationState.Recording -> stop()
            DictationState.Starting -> cancel()
            is DictationState.Transcribing -> Unit
            else -> start()
        }
    }

    fun start() {
        if (disposed || isActive()) return
        discard()
        val generation = ++generation
        val buffer = PcmBuffer(maxSeconds * sampleRate)
        val uploader = DictationUploader(
            api = api,
            buffer = buffer,
            sampleRate = sampleRate,
            listener = object : DictationUploader.Listener {
                override fun lagging(lagging: Boolean) {
                    if (generation != this@DictationController.generation) return
                    this@DictationController.lagging = lagging
                    publishProgress()
                }

                override fun stopCapture() {
                    if (generation != this@DictationController.generation) return
                    stop(InterruptReason.Limit)
                }

                override fun done(text: String) {
                    if (generation != this@DictationController.generation) return
                    finishWith(if (text.isEmpty()) DictationState.Empty else DictationState.Result(text))
                }

                override fun failed(failure: UploadFailure) {
                    if (generation != this@DictationController.generation) return
                    fail(failure)
                }
            },
            scope = scope,
            clock = clock,
        )
        val recorder = createRecorder()
        this.buffer = buffer
        this.uploader = uploader
        this.recorder = recorder
        level = 0f
        lagging = false
        _state.value = DictationState.Starting
        // The dictation opens while the microphone does, so the first words
        // are buffered instead of waiting for the round trip.
        uploader.start()
        scope.launch {
            try {
                recorder.start(sampleRate, sinkFor(generation, buffer, uploader))
            } catch (e: CancellationException) {
                // The scope went away (the screen's ViewModel was cleared)
                // while the microphone was opening; it may already be open.
                release(recorder)
                throw e
            } catch (e: Exception) {
                // An upload failure may already have ended this dictation
                // while the microphone was opening; that is the one to show.
                if (generation != this@DictationController.generation ||
                    _state.value != DictationState.Starting
                ) {
                    return@launch
                }
                this@DictationController.recorder = null
                uploader.cancel()
                buffer.release()
                this@DictationController.buffer = null
                this@DictationController.uploader = null
                _state.value = DictationState.Failed(
                    code = (e as? VoiceRecorderException)?.code ?: VoiceCodes.MIC_UNAVAILABLE,
                    retryable = false,
                    message = e.message,
                    hasAudio = false,
                )
                return@launch
            }
            if (generation != this@DictationController.generation) {
                release(recorder)
                return@launch
            }
            if (_state.value == DictationState.Starting) publishRecording()
        }
    }

    /** Stop recording and transcribe what was captured. */
    fun stop(reason: InterruptReason? = null) {
        when (_state.value) {
            DictationState.Starting -> {
                cancel()
                return
            }
            is DictationState.Recording -> Unit
            else -> return
        }
        val generation = generation
        _state.value = DictationState.Transcribing(reason, lagging)
        val recorder = recorder
        this.recorder = null
        scope.launch {
            withContext(NonCancellable) { runCatching { recorder?.stop() } }
            if (generation != this@DictationController.generation) return@launch
            uploader?.close()
        }
    }

    /** Throw the recording away, on the agent too. */
    fun cancel() {
        generation += 1
        discard()
        _state.value = DictationState.Idle
    }

    /** Retry a failed upload from the buffered audio. */
    fun retry() {
        val failed = _state.value as? DictationState.Failed ?: return
        if (!failed.retryable || !failed.hasAudio) return
        if (uploader?.retry() == true) {
            lagging = false
            _state.value = DictationState.Transcribing(null, false)
        }
    }

    /** Leave a result, an empty result or a failure. */
    fun dismiss() {
        when (_state.value) {
            is DictationState.Result, DictationState.Empty, is DictationState.Failed -> cancel()
            else -> Unit
        }
    }

    /** Leaving the session or switching agents. */
    fun dispose() {
        if (disposed) return
        cancel()
        disposed = true
    }

    private fun sinkFor(
        generation: Int,
        buffer: PcmBuffer,
        uploader: DictationUploader,
    ) = object : VoiceRecorder.Sink {
        override fun samples(samples: ShortArray, length: Int) {
            if (generation != this@DictationController.generation) return
            // Copied into the buffer on the capture thread, before the
            // recorder reuses its array.
            buffer.append(samples, length)
            uploader.onAudio()
            scope.launch {
                if (generation != this@DictationController.generation) return@launch
                if (_state.value !is DictationState.Recording) return@launch
                if (buffer.isFull) stop(InterruptReason.Limit) else publishRecording()
            }
        }

        override fun level(level: Float) {
            scope.launch {
                if (generation != this@DictationController.generation) return@launch
                this@DictationController.level = level
            }
        }

        override fun interrupted(reason: InterruptReason) {
            scope.launch {
                if (generation != this@DictationController.generation) return@launch
                stop(reason)
            }
        }
    }

    private fun isActive(): Boolean = when (_state.value) {
        DictationState.Starting, is DictationState.Recording, is DictationState.Transcribing -> true
        else -> false
    }

    private fun fail(failure: UploadFailure) {
        val generation = generation
        val recorder = recorder
        this.recorder = null
        scope.launch {
            withContext(NonCancellable) { runCatching { recorder?.stop() } }
            if (generation != this@DictationController.generation) return@launch
            _state.value = DictationState.Failed(
                code = failure.code,
                retryable = failure.retryable,
                message = failure.message,
                hasAudio = (buffer?.sampleCount ?: 0) > 0,
            )
        }
    }

    private fun finishWith(state: DictationState) {
        // The agent drops a finished dictation by itself; no delete needed.
        buffer?.release()
        buffer = null
        uploader = null
        _state.value = state
    }

    private fun discard() {
        val recorder = recorder
        this.recorder = null
        release(recorder)
        uploader?.cancel()
        uploader = null
        buffer?.release()
        buffer = null
        level = 0f
        lagging = false
    }

    /**
     * Stop a recorder even when [scope] is already cancelled: a ViewModel's
     * scope is cancelled before `onCleared`, and a plain `launch` there never
     * runs, which would leave the microphone open after the screen is gone.
     */
    private fun release(recorder: VoiceRecorder?) {
        if (recorder == null) return
        scope.launch(NonCancellable) { runCatching { recorder.stop() } }
    }

    private fun publishProgress() {
        when (val current = _state.value) {
            is DictationState.Recording -> publishRecording()
            is DictationState.Transcribing ->
                if (current.lagging != lagging) _state.value = current.copy(lagging = lagging)
            else -> Unit
        }
    }

    private fun publishRecording() {
        val buffer = buffer ?: return
        val elapsedMs = buffer.sampleCount.toLong() * 1000 / sampleRate
        val left = (buffer.capacitySamples - buffer.sampleCount).toDouble() / sampleRate
        _state.value = DictationState.Recording(
            elapsedMs = elapsedMs,
            level = level,
            remainingSeconds = if (left <= COUNTDOWN_SECONDS) ceil(left).toInt().coerceAtLeast(0) else null,
            lagging = lagging,
        )
    }

    private companion object {
        const val COUNTDOWN_SECONDS = 15
    }
}
