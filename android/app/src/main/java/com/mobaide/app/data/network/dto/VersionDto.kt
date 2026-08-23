package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
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

/**
 * Per-CLI model list parsed from the agent's `capabilities.cli.<id>.models`
 * array (populated by the agent's startup enumeration probe, e.g. `agy models`).
 * Returns `null` when the key is absent — an older agent, a CLI without
 * enumeration (Claude), or a failed probe — so callers fall back to their
 * hardcoded model constants. Same loose, never-throwing walk as [cliAvailable].
 */
fun VersionInfoResponse.cliModels(cliId: String): List<String>? = runCatching {
    capabilities["cli"]?.jsonObject
        ?.get(cliId)?.jsonObject
        ?.get("models")?.jsonArray
        ?.mapNotNull { it.jsonPrimitive.contentOrNull }
}.getOrNull()

/**
 * One entry of `capabilities.cli.<id>.model_details`: the reasoning efforts a
 * model supports and (when the catalog names one) its default effort. Plain
 * data class, not `@Serializable` — it is only ever built by the loose walk in
 * [cliModelDetails], never decoded directly off the wire.
 */
data class CliModelDetail(
    val id: String,
    val efforts: List<String>,
    val defaultEffort: String?,
)

/**
 * Per-CLI, per-model reasoning-effort sets parsed from the agent's
 * `capabilities.cli.<id>.model_details` array (today only Codex populates it,
 * from `codex debug models`). Entries without a usable id or with no efforts
 * are dropped; returns `null` when nothing usable remains — an older agent or a
 * failed probe — so callers fall back to their hardcoded effort constants.
 * Same loose, never-throwing walk as [cliAvailable].
 */
fun VersionInfoResponse.cliModelDetails(cliId: String): List<CliModelDetail>? = runCatching {
    capabilities["cli"]?.jsonObject
        ?.get(cliId)?.jsonObject
        ?.get("model_details")?.jsonArray
        ?.mapNotNull { element ->
            runCatching {
                val detail = element.jsonObject
                val id = detail["id"]?.jsonPrimitive?.contentOrNull
                    ?.takeIf { it.isNotBlank() } ?: return@runCatching null
                val efforts = detail["efforts"]?.jsonArray
                    ?.mapNotNull { it.jsonPrimitive.contentOrNull?.takeIf(String::isNotBlank) }
                    ?.takeIf { it.isNotEmpty() } ?: return@runCatching null
                CliModelDetail(
                    id = id,
                    efforts = efforts,
                    defaultEffort = detail["default_effort"]?.jsonPrimitive?.contentOrNull
                        ?.takeIf { it.isNotBlank() },
                )
            }.getOrNull()
        }
        ?.takeIf { it.isNotEmpty() }
}.getOrNull()
