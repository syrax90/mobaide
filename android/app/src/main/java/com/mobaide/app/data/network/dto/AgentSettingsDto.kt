package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * The agent's own settings (PROTOCOL.md "keep the computer awake"), pinned by
 * `tests/protocol_fixtures/dto/agent/AgentSettingsResponse.json`. `state` is
 * an open vocabulary: a value this client does not know reads as "not active".
 */
@Serializable
data class KeepAwakeStatus(
    val enabled: Boolean,
    val state: String,
    val message: String? = null,
) {
    companion object {
        const val STATE_OFF = "off"
        const val STATE_ACTIVE = "active"
        const val STATE_FAILED = "failed"
        const val STATE_UNSUPPORTED = "unsupported"
    }
}

/** `GET /api/agent/settings`, every settings write, and the `agent_settings.changed` payload. */
@Serializable
data class AgentSettingsResponse(
    val keep_awake: KeepAwakeStatus,
)

/** `PUT /api/agent/settings/keep_awake`; answers the full [AgentSettingsResponse]. */
@Serializable
data class KeepAwakeSettingsRequest(
    val enabled: Boolean,
)
