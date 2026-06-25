package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

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

/**
 * Per-CLI availability parsed from the agent's `capabilities.cli.<id>.available`
 * flag (e.g. `cliAvailable("claude")` / `cliAvailable("codex")`). Returns `null`
 * when the key is absent — an older agent that predates this field, or any
 * unexpected shape — so callers can treat "unknown" as optimistically available
 * rather than disabling a mode whose CLI is in fact installed. The loose
 * `JsonObject` walk (not strict typed decoding) keeps an old agent's
 * `capabilities: {}` and any future sibling keys from ever throwing here.
 */
fun VersionInfoResponse.cliAvailable(cliId: String): Boolean? = runCatching {
    capabilities["cli"]?.jsonObject
        ?.get(cliId)?.jsonObject
        ?.get("available")?.jsonPrimitive?.booleanOrNull
}.getOrNull()
