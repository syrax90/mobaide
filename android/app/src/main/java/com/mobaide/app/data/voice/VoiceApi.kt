package com.mobaide.app.data.voice

data class VoiceDictationOpened(
    val dictationId: String,
    val sampleRate: Int,
    val maxSeconds: Int,
    val maxChunkBytes: Int,
)

/** [state] is `done` ([text] set) or `transcribing` (ask again); an open vocabulary. */
data class VoiceFinishResult(val state: String, val text: String?)

/** The dictation half of the agent's voice API, as the uploader sees it. */
interface VoiceApi {
    suspend fun create(): VoiceDictationOpened
    suspend fun audio(dictationId: String, seq: Int, pcm16B64: String)
    suspend fun finish(dictationId: String, lastSeq: Int): VoiceFinishResult

    /**
     * Delete the dictation in the background and ignore every failure: the
     * route is idempotent, and an older relay drops the connection after its
     * 204, so even a "failure" usually means it worked.
     */
    fun discard(dictationId: String)
}

/**
 * A refused or failed voice request. [code] is the agent's `detail.code` (an
 * open vocabulary); [network] marks a request that never got an answer.
 */
class VoiceApiException(
    val status: Int?,
    val code: String?,
    message: String,
    val detailMessage: String? = null,
    val expectedSeq: Int? = null,
    val network: Boolean = false,
    cause: Throwable? = null,
) : Exception(message, cause)

object VoiceCodes {
    const val NOT_READY = "voice_not_ready"
    const val BUSY = "voice_busy"
    const val UNSUPPORTED = "voice_unsupported"
    const val DICTATION_NOT_FOUND = "dictation_not_found"
    const val SEQ_GAP = "seq_gap"
    const val DICTATION_FINISHED = "dictation_finished"
    const val TOO_LONG = "too_long"
    const val CHUNK_TOO_LARGE = "chunk_too_large"
    const val BAD_AUDIO = "bad_audio"
    const val TRANSCRIPTION_FAILED = "transcription_failed"

    /** Client-side codes, never sent by the agent. */
    const val NETWORK = "network"
    const val TRANSCRIPTION_TIMEOUT = "transcription_timeout"
    const val MIC_PERMISSION_DENIED = "mic_permission_denied"
    const val MIC_UNAVAILABLE = "mic_unavailable"
    const val GENERIC = "generic"
}
