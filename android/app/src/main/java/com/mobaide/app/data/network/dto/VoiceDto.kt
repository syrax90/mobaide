package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * The agent's voice input API (PROTOCOL.md "on-PC voice input"), pinned by
 * `tests/protocol_fixtures/dto/agent/Voice*.json`. `state`,
 * `host.recommendation`, `reasons[].code` and `error.code` are open
 * vocabularies kept as strings: an unknown value reads as "voice unavailable".
 */
@Serializable
data class VoiceModelInfo(
    val id: String,
    val name: String,
    val languages: List<String> = emptyList(),
    val download_bytes: Long,
    val disk_bytes: Long,
    val ram_bytes: Long,
    val notice: String? = null,
)

@Serializable
data class VoiceProgress(
    val done_bytes: Long,
    val total_bytes: Long,
)

@Serializable
data class VoiceHostReason(
    val code: String,
    val message: String,
)

@Serializable
data class VoiceHostInfo(
    val ram_total_bytes: Long? = null,
    val disk_free_bytes: Long? = null,
    val cpu_count: Int? = null,
    val recommendation: String,
    val reasons: List<VoiceHostReason> = emptyList(),
)

@Serializable
data class VoiceErrorInfo(
    val code: String,
    val message: String,
)

/** `GET /api/voice/status`, the install/cancel/remove answers and the `voice.changed` payload. */
@Serializable
data class VoiceStatusResponse(
    val state: String,
    val enabled: Boolean,
    val repairing: Boolean = false,
    val model: VoiceModelInfo,
    val progress: VoiceProgress? = null,
    val host: VoiceHostInfo,
    val speed_factor: Double? = null,
    val error: VoiceErrorInfo? = null,
    /** Transcript cleanup, one choice on the agent shared by all its clients. */
    val cleanup_enabled: Boolean = false,
) {
    companion object {
        const val STATE_UNSUPPORTED = "unsupported"
        const val STATE_NOT_INSTALLED = "not_installed"
        const val STATE_QUEUED = "queued"
        const val STATE_DOWNLOADING = "downloading"
        const val STATE_VERIFYING = "verifying"
        const val STATE_READY = "ready"
        const val STATE_FAILED = "failed"
        const val RECOMMENDATION_LOW_RESOURCES = "low_resources"
    }
}

@Serializable
data class VoiceInstallRequest(
    val model_id: String? = null,
)

@Serializable
data class VoiceDictationCreateRequest(
    val session_id: String? = null,
)

@Serializable
data class VoiceDictationResponse(
    val dictation_id: String,
    val sample_rate: Int,
    val max_seconds: Int,
    val max_chunk_bytes: Int,
)

/** `pcm16_b64`: standard padded base64 with no line breaks (`java.util.Base64`). */
@Serializable
data class VoiceDictationAudioRequest(
    val seq: Int,
    val pcm16_b64: String,
)

@Serializable
data class VoiceDictationAudioResponse(
    val seq: Int,
    val duration_ms: Long,
)

/** `last_seq`: the highest seq the agent accepted with a 200; -1 when none was. */
@Serializable
data class VoiceDictationFinishRequest(
    val last_seq: Int,
)

/**
 * `POST /api/voice/cleanup`: [text] is 1–6000 characters; [kind] is the
 * session kind's wire name, whose CLI the agent runs.
 */
@Serializable
data class VoiceCleanupRequest(
    val text: String,
    val kind: String,
)

/** `PUT /api/voice/cleanup/settings`; answers the full [VoiceStatusResponse]. */
@Serializable
data class VoiceCleanupSettingsRequest(
    val enabled: Boolean,
)

/** The cleaned transcript; the input when there was nothing to clean. */
@Serializable
data class VoiceCleanupResponse(
    val text: String,
)

/** `state` is `done` (text set) or `transcribing` (call finish again). */
@Serializable
data class VoiceDictationResultResponse(
    val dictation_id: String,
    val state: String,
    val text: String? = null,
    val duration_ms: Long,
)
