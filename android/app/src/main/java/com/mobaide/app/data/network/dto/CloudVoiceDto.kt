package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

@Serializable
data class CloudVoiceLimits(
    val sample_rate: Int,
    val max_seconds: Int,
    val max_chunk_bytes: Int,
)

/** The account's audio transcribed today (UTC) against its daily limit. */
@Serializable
data class CloudVoiceUsage(
    val used_seconds: Int,
    val limit_seconds: Int,
    val resets_at: String,
)

/**
 * [state] and the [engines] values are open vocabularies kept as strings: a
 * state this client does not know reads as "not offered", and an engine it
 * does not know as "no choice yet".
 */
@Serializable
data class CloudVoiceStatus(
    val state: String,
    val limits: CloudVoiceLimits,
    val usage: CloudVoiceUsage,
    val engines: Map<String, String>,
) {
    companion object {
        const val STATE_OFF = "off"
        const val STATE_READY = "ready"
        const val STATE_UNAVAILABLE = "unavailable"
        const val STATE_LIMIT_REACHED = "limit_reached"
        const val ENGINE_CLOUD = "cloud"
        const val ENGINE_LOCAL = "local"

        /** What a server without cloud voice input amounts to. */
        val NOT_OFFERED = CloudVoiceStatus(
            state = STATE_OFF,
            limits = CloudVoiceLimits(sample_rate = 16_000, max_seconds = 120, max_chunk_bytes = 65_536),
            usage = CloudVoiceUsage(used_seconds = 0, limit_seconds = 0, resets_at = ""),
            engines = emptyMap(),
        )
    }
}

/** The payload of the `cloud_voice.changed` user event: the whole map of choices. */
@Serializable
data class CloudVoiceChangedPayload(
    val engines: Map<String, String>,
)

@Serializable
data class CloudVoiceEngineRequest(
    val engine: String,
)
