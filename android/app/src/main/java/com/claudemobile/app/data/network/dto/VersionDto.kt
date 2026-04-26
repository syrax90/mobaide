package com.claudemobile.app.data.network.dto

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * Wire shape returned by ``GET /api/version`` on both ``central_server`` and
 * ``claude_agent``. ``latest_agent_version`` / ``manifest_url`` /
 * ``manifest_signature`` are populated only on the central_server side; the
 * agent returns ``null`` for those fields.
 */
@Serializable
data class VersionInfoResponse(
    val name: String,
    val server_version: String,
    val protocol_versions: List<Int>,
    val min_client_version: Int,
    val capabilities: JsonObject = JsonObject(emptyMap()),
    val latest_agent_version: String? = null,
    val manifest_url: String? = null,
    val manifest_signature: String? = null,
)
