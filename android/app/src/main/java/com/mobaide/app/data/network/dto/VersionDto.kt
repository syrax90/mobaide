package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
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
 * Whether the agent's startup model enumeration was still running when this
 * response was built (`capabilities.cli.<id>.models_pending`). The absence of
 * `models`/`model_details` is then provisional, not authoritative: callers
 * keep persisted catalog selections instead of reconciling them away, and may
 * re-probe. Absent key (old agents, finished warmup) or any unreadable shape
 * reads as `false`. Same loose, never-throwing walk as [cliAvailable].
 */
fun VersionInfoResponse.cliModelsPending(cliId: String): Boolean = runCatching {
    capabilities["cli"]?.jsonObject
        ?.get(cliId)?.jsonObject
        ?.get("models_pending")?.jsonPrimitive?.booleanOrNull
}.getOrNull() == true

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

/**
 * Whether the agent advertises `capabilities.git.<key>` as `true`. The opposite
 * default from [cliAvailable]: a mutating git control is shown only on an
 * explicit `true`, so an older agent (or an unreadable probe) keeps the
 * Changes viewer read-only rather than offering a Stage that would 404.
 */
fun VersionInfoResponse.gitCapability(key: String): Boolean = runCatching {
    capabilities["git"]?.jsonObject
        ?.get(key)?.jsonPrimitive
        // A JSON boolean only: `booleanOrNull` would also read the string
        // "true", which web and iOS reject.
        ?.let { !it.isString && it.booleanOrNull == true }
}.getOrNull() == true

/** `capabilities.voice`: what dictation needs to know before it starts. */
data class VoiceCapability(
    val sampleRate: Int,
    val maxSeconds: Int,
    val maxChunkBytes: Int,
)

/**
 * The agent's voice input limits, or null when it does not offer voice. The
 * key is present whatever the install state (`GET /api/voice/status` tells
 * that), so null means an agent too old for voice: the mic stays hidden.
 */
fun VersionInfoResponse.voiceCapability(): VoiceCapability? = runCatching {
    val voice = capabilities["voice"]?.jsonObject ?: return@runCatching null
    val dictation = voice["dictation"]?.jsonPrimitive ?: return@runCatching null
    if (dictation.isString || dictation.booleanOrNull != true) return@runCatching null
    fun positive(key: String): Int? =
        voice[key]?.jsonPrimitive?.takeIf { !it.isString }?.intOrNull?.takeIf { it > 0 }
    VoiceCapability(
        sampleRate = positive("sample_rate") ?: return@runCatching null,
        maxSeconds = positive("max_seconds") ?: return@runCatching null,
        maxChunkBytes = positive("max_chunk_bytes") ?: return@runCatching null,
    )
}.getOrNull()
