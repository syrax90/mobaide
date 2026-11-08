package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.VoiceCapability
import com.mobaide.app.data.network.dto.VoiceDictationAudioRequest
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import com.mobaide.app.data.network.dto.voiceCapability
import com.mobaide.app.data.voice.VoiceApi
import com.mobaide.app.data.voice.VoiceApiException
import com.mobaide.app.data.voice.VoiceDictationOpened
import com.mobaide.app.data.voice.VoiceFinishResult
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import java.io.IOException
import java.util.concurrent.atomic.AtomicLong

/**
 * The status is reconciled by `voice.changed`, a full snapshot the agent also
 * republishes on every events-channel connect, so an agent switch needs no
 * REST refresh; leaving the connected state clears it.
 */
class VoiceRepository(
    private val restApi: RestApi,
    private val eventBus: AgentEventBus,
    sessionManager: SessionManager,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val _status = MutableStateFlow<VoiceStatusResponse?>(null)
    val status: StateFlow<VoiceStatusResponse?> = _status.asStateFlow()

    /** Bumped by every `voice.changed`, so an older GET answer never overwrites one. */
    private val events = AtomicLong()

    init {
        scope.launch {
            eventBus.stream<AgentEvent.VoiceChanged>().collect {
                events.incrementAndGet()
                _status.value = it.status
            }
        }
        scope.launch {
            sessionManager.authState.collect { state ->
                if (state != SessionManager.AuthState.FullyConnected) _status.value = null
            }
        }
    }

    /** `capabilities.voice` of the connected agent; null when it has no voice. */
    suspend fun capability(): VoiceCapability? = restApi.getAgentVersion().voiceCapability()

    /** A snapshot that arrived while this request was in flight is newer and wins. */
    suspend fun refresh(): VoiceStatusResponse {
        val before = events.get()
        val answer = voiceCall { restApi.getVoiceStatus() }
        if (events.get() == before) _status.value = answer
        return answer
    }

    suspend fun install(): VoiceStatusResponse = write { restApi.installVoice() }

    /** Stop an install; while repairing, only the runtime download stops. */
    suspend fun cancelInstall(): VoiceStatusResponse = write { restApi.cancelVoiceInstall() }

    /** Turn voice off and free the disk. */
    suspend fun remove(): VoiceStatusResponse = write { restApi.removeVoice() }

    suspend fun reinstall(): VoiceStatusResponse {
        remove()
        return install()
    }

    /**
     * The status shows [enabled] at once and goes back if the agent refuses.
     * A snapshot that arrives meanwhile wins over both the rollback and the answer.
     */
    suspend fun setCleanup(enabled: Boolean): VoiceStatusResponse {
        val previous = _status.value
        val optimistic = previous?.copy(cleanup_enabled = enabled)
        if (optimistic != null) _status.value = optimistic
        return try {
            voiceCall { restApi.setVoiceCleanupSettings(enabled) }
                .also { _status.compareAndSet(optimistic, it) }
        } catch (e: VoiceApiException) {
            if (optimistic != null) _status.compareAndSet(optimistic, previous)
            throw e
        }
    }

    /**
     * The CLI of session kind [kind] (a wire name) cleans up [text] on the
     * connected agent's computer (within 20 s).
     */
    suspend fun cleanup(text: String, kind: String): String =
        voiceCall { restApi.cleanupVoiceTranscript(text, kind).text }

    /** The dictation routes of the connected agent, for the uploader. */
    fun dictationApi(sessionId: String?): VoiceApi = object : VoiceApi {
        override suspend fun create(): VoiceDictationOpened = voiceCall {
            val body = restApi.createVoiceDictation(sessionId)
            VoiceDictationOpened(body.dictation_id, body.sample_rate, body.max_seconds, body.max_chunk_bytes)
        }

        override suspend fun audio(dictationId: String, seq: Int, pcm16B64: String) {
            voiceCall { restApi.appendVoiceAudio(dictationId, VoiceDictationAudioRequest(seq, pcm16B64)) }
        }

        override suspend fun finish(dictationId: String, lastSeq: Int): VoiceFinishResult = voiceCall {
            val body = restApi.finishVoiceDictation(dictationId, lastSeq)
            VoiceFinishResult(body.state, body.text)
        }

        override fun discard(dictationId: String) {
            scope.launch { runCatching { restApi.deleteVoiceDictation(dictationId) } }
        }
    }

    private suspend fun write(call: suspend () -> VoiceStatusResponse): VoiceStatusResponse =
        voiceCall(call).also { _status.value = it }
}

/** Any failure of a voice request as a [VoiceApiException]; cancellation passes through. */
internal suspend fun <T> voiceCall(call: suspend () -> T): T = try {
    call()
} catch (e: CancellationException) {
    throw e
} catch (e: Exception) {
    throw mapVoiceError(e)
}

/**
 * Voice refusals carry `{"detail": {"code", "message", …}}` on any status;
 * `expected_seq` rides along with `seq_gap`. A request that never got an
 * answer is `network`.
 */
internal fun mapVoiceError(error: Throwable): VoiceApiException = when (error) {
    is VoiceApiException -> error
    is ApiException -> {
        val detail = extractDetail(error.errorBody) as? JsonObject
        VoiceApiException(
            status = error.code,
            code = detail?.get("code")?.jsonPrimitive?.contentOrNull,
            message = error.message ?: "HTTP ${error.code}",
            detailMessage = detail?.get("message")?.jsonPrimitive?.contentOrNull,
            expectedSeq = detail?.get("expected_seq")?.jsonPrimitive?.intOrNull,
            cause = error,
        )
    }
    is IOException -> VoiceApiException(
        status = null,
        code = null,
        message = error.message ?: "Network error",
        network = true,
        cause = error,
    )
    else -> VoiceApiException(status = null, code = null, message = error.message ?: "Voice request failed", cause = error)
}
