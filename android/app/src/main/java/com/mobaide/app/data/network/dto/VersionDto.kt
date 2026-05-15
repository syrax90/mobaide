package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * Wire shape returned by ``GET /api/version`` on both ``central_server`` and
 * ``mobaide_agent``. ``latest_agent_version`` is populated only on the
 * central_server side (the agent returns ``null``) and drives the
 * "agent outdated" banner. The signed manifest body itself is delivered to
 * the agent inline over the relay WebSocket (``agent_manifest_update``
 * control frame) and is never carried in this REST response.
 */
@Serializable
data class VersionInfoResponse(
    val name: String,
    val server_version: String,
    val protocol_versions: List<Int>,
    val min_client_version: Int,
    val capabilities: JsonObject = JsonObject(emptyMap()),
    val latest_agent_version: String? = null,
)
