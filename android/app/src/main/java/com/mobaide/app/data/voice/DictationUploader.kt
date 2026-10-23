package com.mobaide.app.data.voice

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.IOException
import kotlin.random.Random

/** A failed upload; [retryable] means a Retry from the buffered audio can work. */
data class UploadFailure(val code: String, val retryable: Boolean, val message: String?)

/**
 * A single pump coroutine is the only code that issues requests, so exactly
 * one audio request is in flight per dictation and `finish` is only ever sent
 * after the last chunk was acknowledged. [onAudio], [close] and [cancel] only
 * set state and wake the pump.
 */
class DictationUploader(
    private val api: VoiceApi,
    private val buffer: PcmBuffer,
    private val sampleRate: Int,
    private val listener: Listener,
    private val scope: CoroutineScope,
    private val clock: Clock = Clock.System,
) {
    interface Listener {
        fun lagging(lagging: Boolean)

        /** The agent's limit was reached: stop recording and transcribe. */
        fun stopCapture()
        fun done(text: String)
        fun failed(failure: UploadFailure)
    }

    interface Clock {
        fun now(): Long
        fun random(): Double

        object System : Clock {
            override fun now(): Long = java.lang.System.currentTimeMillis()
            override fun random(): Double = Random.nextDouble()
        }
    }

    private class Fatal(val failure: UploadFailure) : Exception(failure.code)

    private sealed interface FinishOutcome {
        data class Text(val text: String) : FinishOutcome
        data object Resync : FinishOutcome
    }

    private data class Classified(
        val transient: Boolean,
        val status: Int?,
        val code: String?,
        val message: String?,
        val expectedSeq: Int?,
    )

    private val wake = Channel<Unit>(Channel.CONFLATED)
    private var job: Job? = null
    private var dictationId: String? = null
    private var chunkSamples = 0
    private var nextSeq = 0

    /** Highest seq the agent answered 200 for: what `finish` reports. */
    private var accepted = -1
    private var closed = false
    private var closedAt: Long? = null
    private var serverCap = false
    private var restarts = 0
    private var resyncs = 0
    private var latched = false
    private var cancelled = false
    private var isLagging = false

    fun start() = launch()

    /** New samples are in the buffer; safe from any thread. */
    fun onAudio() {
        wake.trySend(Unit)
    }

    /** Recording stopped: upload the tail and transcribe. */
    fun close() {
        if (!closed) {
            closed = true
            closedAt = clock.now()
        }
        wake.trySend(Unit)
    }

    /**
     * Resume after a retryable failure, from the buffered audio. A live
     * dictation continues where it stopped (or re-asks `finish`, whose result
     * the agent keeps); a latched or never-opened one starts over.
     */
    fun retry(): Boolean {
        if (job?.isActive == true || cancelled) return false
        restarts = 0
        resyncs = 0
        if (latched || dictationId == null) {
            dictationId?.let(api::discard)
            dictationId = null
            nextSeq = 0
            accepted = -1
            latched = false
            serverCap = false
        }
        closed = true
        closedAt = clock.now()
        launch()
        return true
    }

    /** Abort everything and delete the dictation on the agent; never reports. */
    fun cancel() {
        if (cancelled) return
        cancelled = true
        job?.cancel()
        dictationId?.let(api::discard)
        dictationId = null
    }

    private fun launch() {
        job = scope.launch { run() }
    }

    private suspend fun run() {
        try {
            if (dictationId == null) open()
            while (true) {
                val final = closed || serverCap
                val sealed = buffer.sealedChunks(chunkSamples, final)
                if (!serverCap && nextSeq < sealed) {
                    send(nextSeq)
                    continue
                }
                if (final) {
                    when (val outcome = finishLoop()) {
                        FinishOutcome.Resync -> continue
                        is FinishOutcome.Text -> {
                            if (!cancelled) listener.done(outcome.text)
                            return
                        }
                    }
                }
                wake.receive()
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Fatal) {
            if (!cancelled) listener.failed(e.failure)
        } catch (e: Exception) {
            if (!cancelled) listener.failed(UploadFailure(VoiceCodes.GENERIC, true, e.message))
        }
    }

    private suspend fun open() {
        var attempt = 0
        var firstFailure: Long? = null
        while (true) {
            // Never cancelled midway: the id it returns must reach a delete, or
            // the dictation would hold one of the agent's two slots.
            val result = withContext(NonCancellable) {
                runCatching { withTimeoutOrNull(CREATE_TIMEOUT_MS) { api.create() } }
            }
            val opened = result.getOrNull()
            if (cancelled) {
                opened?.let { api.discard(it.dictationId) }
                throw CancellationException("dictation cancelled")
            }
            if (opened == null) {
                val failure = result.exceptionOrNull()?.let(::classify) ?: TIMEOUT
                if (!failure.transient) throw fatal(failure)
                val since = firstFailure ?: clock.now().also { firstFailure = it }
                checkStall(since, failure)
                setLagging(true)
                backoff(attempt++)
                continue
            }
            val bytes = if (opened.sampleRate == sampleRate) {
                Pcm16.chunkBytes(opened.sampleRate, opened.maxChunkBytes)
            } else {
                null
            }
            if (bytes == null) {
                api.discard(opened.dictationId)
                throw Fatal(UploadFailure(VoiceCodes.GENERIC, false, null))
            }
            chunkSamples = bytes / 2
            buffer.shrinkCapacity(opened.maxSeconds * opened.sampleRate)
            dictationId = opened.dictationId
            setLagging(false)
            if (buffer.isFull && !closed) listener.stopCapture()
            return
        }
    }

    private suspend fun send(seq: Int) {
        val id = requireDictation()
        val payload = buffer.payload(seq, chunkSamples)
        var attempt = 0
        var firstFailure: Long? = null
        while (true) {
            val failure = try {
                val answered = withTimeoutOrNull(AUDIO_TIMEOUT_MS) {
                    api.audio(id, seq, payload)
                    true
                }
                if (answered == true) {
                    accepted = seq
                    nextSeq = seq + 1
                    setLagging(false)
                    return
                }
                TIMEOUT
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                classify(e)
            }
            if (failure.transient) {
                val since = firstFailure ?: clock.now().also { firstFailure = it }
                checkStall(since, failure)
                setLagging(true)
                backoff(attempt++)
                continue
            }
            when (failure.code) {
                VoiceCodes.DICTATION_NOT_FOUND -> {
                    restart()
                    return
                }
                VoiceCodes.SEQ_GAP -> {
                    resync(failure.expectedSeq)
                    return
                }
                VoiceCodes.TOO_LONG -> {
                    serverCap = true
                    listener.stopCapture()
                    return
                }
                VoiceCodes.TRANSCRIPTION_FAILED -> {
                    latched = true
                    throw fatal(failure)
                }
                else -> throw fatal(failure)
            }
        }
    }

    private suspend fun finishLoop(): FinishOutcome {
        val id = requireDictation()
        val deadline = clock.now() + FINISH_DEADLINE_MS
        var attempt = 0
        var firstFailure: Long? = null
        while (true) {
            if (clock.now() > deadline) {
                throw Fatal(UploadFailure(VoiceCodes.TRANSCRIPTION_TIMEOUT, true, null))
            }
            val askedAt = clock.now()
            val failure = try {
                val result = withTimeoutOrNull(FINISH_TIMEOUT_MS) { api.finish(id, accepted) }
                if (result != null) {
                    setLagging(false)
                    if (result.state == STATE_DONE) {
                        return FinishOutcome.Text(result.text.orEmpty().trim())
                    }
                    attempt = 0
                    firstFailure = null
                    // `transcribing` normally follows the agent's own 20 s wait;
                    // an instant answer must not turn this into a hot loop.
                    val elapsed = clock.now() - askedAt
                    if (elapsed < MIN_FINISH_POLL_MS) delay(MIN_FINISH_POLL_MS - elapsed)
                    continue
                }
                TIMEOUT
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                classify(e)
            }
            if (failure.transient) {
                val since = firstFailure ?: clock.now().also { firstFailure = it }
                checkStall(since, failure)
                setLagging(true)
                backoff(attempt++)
                continue
            }
            when (failure.code) {
                VoiceCodes.SEQ_GAP -> {
                    resync(failure.expectedSeq)
                    return FinishOutcome.Resync
                }
                VoiceCodes.DICTATION_NOT_FOUND -> {
                    restart()
                    return FinishOutcome.Resync
                }
                VoiceCodes.TRANSCRIPTION_FAILED -> {
                    latched = true
                    throw fatal(failure)
                }
                else -> throw fatal(failure)
            }
        }
    }

    private fun resync(expected: Int?) {
        resyncs += 1
        if (resyncs > MAX_RESYNCS || expected == null || expected < 0 || expected > nextSeq) {
            throw Fatal(UploadFailure(VoiceCodes.SEQ_GAP, false, null))
        }
        nextSeq = expected
        accepted = expected - 1
    }

    private suspend fun restart() {
        restarts += 1
        if (restarts > MAX_RESTARTS) {
            throw Fatal(UploadFailure(VoiceCodes.DICTATION_NOT_FOUND, true, null))
        }
        dictationId?.let(api::discard)
        dictationId = null
        nextSeq = 0
        accepted = -1
        resyncs = 0
        serverCap = false
        open()
    }

    private fun classify(error: Throwable): Classified = when (error) {
        is VoiceApiException -> Classified(
            transient = error.network ||
                (error.status != null && error.status >= 500 &&
                    (error.code == null || error.code == VoiceCodes.BUSY)),
            status = error.status,
            code = error.code,
            message = error.detailMessage ?: error.message,
            expectedSeq = error.expectedSeq,
        )
        is IOException -> Classified(true, null, null, error.message, null)
        else -> Classified(false, null, null, error.message, null)
    }

    private fun fatal(failure: Classified) = Fatal(
        UploadFailure(
            code = failure.code ?: if (failure.transient) VoiceCodes.NETWORK else VoiceCodes.GENERIC,
            retryable = isRetryable(failure),
            message = failure.message,
        ),
    )

    private fun checkStall(firstFailure: Long, failure: Classified) {
        val closedAt = closedAt ?: return
        if (clock.now() - maxOf(firstFailure, closedAt) > STALL_LIMIT_MS) {
            throw Fatal(UploadFailure(VoiceCodes.NETWORK, true, failure.message))
        }
    }

    private suspend fun backoff(attempt: Int) {
        val base = BACKOFF_MS[minOf(attempt, BACKOFF_MS.size - 1)]
        val jitter = base * 0.2 * (clock.random() * 2 - 1)
        delay((base + jitter).toLong())
    }

    private fun setLagging(lagging: Boolean) {
        if (isLagging == lagging) return
        isLagging = lagging
        listener.lagging(lagging)
    }

    private fun requireDictation(): String =
        dictationId ?: throw Fatal(UploadFailure(VoiceCodes.GENERIC, true, null))

    private companion object {
        /** Above the relay's 30 s wait, so an id the agent did issue is never thrown away. */
        const val CREATE_TIMEOUT_MS = 35_000L
        const val AUDIO_TIMEOUT_MS = 15_000L

        /** The agent waits up to 20 s and the relay 30 s before answering `finish`. */
        const val FINISH_TIMEOUT_MS = 35_000L

        /** After the user stops, how long a stalled upload may make no progress. */
        const val STALL_LIMIT_MS = 30_000L

        /** The agent keeps a finished result for 2 minutes. */
        const val FINISH_DEADLINE_MS = 120_000L
        const val MIN_FINISH_POLL_MS = 1_000L
        const val MAX_RESTARTS = 2
        const val MAX_RESYNCS = 3
        val BACKOFF_MS = longArrayOf(500, 1_000, 2_000, 4_000)
        const val STATE_DONE = "done"
        val TIMEOUT = Classified(true, null, null, "timeout", null)

        fun isRetryable(failure: Classified): Boolean {
            if (failure.transient) return true
            return when (failure.code) {
                VoiceCodes.BUSY, VoiceCodes.TRANSCRIPTION_FAILED, VoiceCodes.DICTATION_NOT_FOUND -> true
                VoiceCodes.NOT_READY, VoiceCodes.UNSUPPORTED, VoiceCodes.BAD_AUDIO,
                VoiceCodes.CHUNK_TOO_LARGE, VoiceCodes.DICTATION_FINISHED,
                VoiceCodes.SEQ_GAP, VoiceCodes.TOO_LONG -> false
                else -> failure.status != null && failure.status >= 500
            }
        }
    }
}
