package com.mobaide.app.testing

import com.mobaide.app.data.voice.DictationUploader
import com.mobaide.app.data.voice.InterruptReason
import com.mobaide.app.data.voice.UploadFailure
import com.mobaide.app.data.voice.VoiceApi
import com.mobaide.app.data.voice.VoiceApiException
import com.mobaide.app.data.voice.VoiceDictationOpened
import com.mobaide.app.data.voice.VoiceFinishResult
import com.mobaide.app.data.voice.VoiceRecorder
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.test.TestCoroutineScheduler

/**
 * Shared doubles for the dictation uploader and controller tests: one scripted
 * agent, one virtual clock, so both suites drive the same rules.
 */
sealed interface Reply<out T> {
    data class Ok<T>(val value: T) : Reply<T>
    data class Fail(val error: Exception) : Reply<Nothing>

    /** Answered by the test; [aborted] once the caller gave up on it. */
    class Hold<T> : Reply<T> {
        val answer = CompletableDeferred<T>()
        var entered = false
        var aborted = false
    }
}

fun networkError() = VoiceApiException(status = null, code = null, message = "offline", network = true)

fun refusal(status: Int, code: String?, detailMessage: String? = null, expectedSeq: Int? = null) =
    VoiceApiException(
        status = status,
        code = code,
        message = "HTTP $status",
        detailMessage = detailMessage,
        expectedSeq = expectedSeq,
    )

/**
 * The agent's dictation API, scripted per method: queued replies first, then
 * the method's fallback. Every call lands in [log] (`create`, `audio d1#0`,
 * `finish d1@2`, `remove d1`) and its virtual time in [times].
 */
class FakeVoiceApi(private val scheduler: TestCoroutineScheduler) : VoiceApi {
    val log = mutableListOf<String>()
    val times = mutableListOf<Long>()
    val payloads = mutableMapOf<String, String>()
    val createReplies = ArrayDeque<Reply<VoiceDictationOpened>>()
    val audioReplies = ArrayDeque<Reply<Unit>>()
    val finishReplies = ArrayDeque<Reply<VoiceFinishResult>>()
    var sampleRate = 16_000
    var maxSeconds = 120
    var maxChunkBytes = 8
    var text = "hello world"
    var audioFallback: () -> Reply<Unit> = { Reply.Ok(Unit) }
    var finishFallback: () -> Reply<VoiceFinishResult> = { Reply.Ok(VoiceFinishResult("done", text)) }
    private var created = 0

    fun opened(dictationId: String) = VoiceDictationOpened(dictationId, sampleRate, maxSeconds, maxChunkBytes)

    fun timesOf(entry: String): List<Long> = log.indices.filter { log[it] == entry }.map { times[it] }

    private fun record(entry: String) {
        log += entry
        times += scheduler.currentTime
    }

    override suspend fun create(): VoiceDictationOpened {
        record("create")
        created += 1
        return answer(createReplies.removeFirstOrNull() ?: Reply.Ok(opened("d$created")))
    }

    override suspend fun audio(dictationId: String, seq: Int, pcm16B64: String) {
        record("audio $dictationId#$seq")
        payloads["$dictationId#$seq"] = pcm16B64
        answer(audioReplies.removeFirstOrNull() ?: audioFallback())
    }

    override suspend fun finish(dictationId: String, lastSeq: Int): VoiceFinishResult {
        record("finish $dictationId@$lastSeq")
        return answer(finishReplies.removeFirstOrNull() ?: finishFallback())
    }

    override fun discard(dictationId: String) {
        record("remove $dictationId")
    }

    private suspend fun <T> answer(reply: Reply<T>): T = when (reply) {
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

/** Time is the test scheduler's; [randomValue] pins the backoff jitter. */
class FakeUploaderClock(private val scheduler: TestCoroutineScheduler) : DictationUploader.Clock {
    var randomValue = 0.5

    override fun now(): Long = scheduler.currentTime

    override fun random(): Double = randomValue
}

class RecordingListener : DictationUploader.Listener {
    val events = mutableListOf<String>()
    var failure: UploadFailure? = null

    override fun lagging(lagging: Boolean) {
        events += "lagging $lagging"
    }

    override fun stopCapture() {
        events += "stopCapture"
    }

    override fun done(text: String) {
        events += "done $text"
    }

    override fun failed(failure: UploadFailure) {
        events += "failed ${failure.code}"
        this.failure = failure
    }
}

/** The microphone, driven by the test through [sink]. */
class FakeRecorder : VoiceRecorder {
    var sink: VoiceRecorder.Sink? = null
    var sampleRate: Int? = null
    var stops = 0
    var startFailure: Exception? = null
    var startGate: CompletableDeferred<Unit>? = null

    override suspend fun start(sampleRate: Int, sink: VoiceRecorder.Sink) {
        this.sampleRate = sampleRate
        this.sink = sink
        startGate?.await()
        startFailure?.let { throw it }
    }

    override suspend fun stop() {
        stops += 1
    }

    fun speak(seconds: Int, rate: Int = 16_000) {
        val samples = ShortArray(seconds * rate) { ((it % 200) - 100).toShort() }
        sink?.samples(samples, samples.size)
    }

    fun interrupt(reason: InterruptReason) {
        sink?.interrupted(reason)
    }
}

/** `count` distinct, non-zero samples. */
fun ramp(count: Int, from: Int = 1): ShortArray = ShortArray(count) { (from + it).toShort() }
