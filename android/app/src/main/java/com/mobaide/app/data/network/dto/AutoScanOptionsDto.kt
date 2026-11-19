@file:OptIn(ExperimentalSerializationApi::class)

package com.mobaide.app.data.network.dto

import kotlinx.serialization.EncodeDefault
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.descriptors.nullable
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonNull

/*
 * The launch options an Auto Scan keeps per agent kind (PROTOCOL.md "Agent
 * options"): the session options minus the worktree, branch, prompt and
 * sign-in fields. Every field is always sent, so the agent never fills a
 * value in from its own defaults.
 */

@Serializable
data class ClaudeScanOptionsDto(
    @EncodeDefault val plan_mode: Boolean = false,
    @EncodeDefault val no_memory: Boolean = false,
    @EncodeDefault val skip_permissions: Boolean = true,
    @EncodeDefault val model: String? = null,
    @EncodeDefault val effort: String? = null,
    @EncodeDefault val fast_mode: Boolean = false,
    @EncodeDefault val ultracode: Boolean = false,
    @EncodeDefault val append_system_prompt: String? = null,
    @EncodeDefault val debug: Boolean = false,
    // null leaves Claude Code's view alone.
    @EncodeDefault val focus_mode: Boolean? = null,
)

@Serializable
data class CodexScanOptionsDto(
    @EncodeDefault val model: String? = null,
    @EncodeDefault val sandbox: String = "workspace-write",
    @EncodeDefault val approval: String = "never",
    @EncodeDefault val reasoning_effort: String? = "high",
    @EncodeDefault val full_auto: Boolean = true,
)

/** [skip_permissions] and [sandbox] are mutually exclusive (the agent answers 422). */
@Serializable
data class AntigravityScanOptionsDto(
    @EncodeDefault val model: String? = null,
    @EncodeDefault val skip_permissions: Boolean = true,
    @EncodeDefault val sandbox: Boolean = false,
    @EncodeDefault val add_dirs: List<String> = emptyList(),
)

@Serializable
data class OpenCodeScanOptionsDto(@EncodeDefault val auto_approve: Boolean = true)

@Serializable
data class CopilotScanOptionsDto(@EncodeDefault val allow_all: Boolean = true)

@Serializable
data class CursorScanOptionsDto(@EncodeDefault val force: Boolean = true)

@Serializable
data class JunieScanOptionsDto(
    @EncodeDefault val plan_mode: Boolean = false,
    @EncodeDefault val brave: Boolean = true,
)

/**
 * Decodes an options object the client cannot read as null, so one bad value
 * never fails the workspace list or the template list that carries it.
 */
open class LenientObjectSerializer<T : Any>(private val inner: KSerializer<T>) : KSerializer<T?> {
    override val descriptor: SerialDescriptor = inner.descriptor.nullable

    override fun deserialize(decoder: Decoder): T? {
        val json = decoder as? JsonDecoder ?: return inner.deserialize(decoder)
        val element = json.decodeJsonElement()
        if (element is JsonNull) return null
        return runCatching { json.json.decodeFromJsonElement(inner, element) }.getOrNull()
    }

    override fun serialize(encoder: Encoder, value: T?) {
        encoder.encodeNullableSerializableValue(inner, value)
    }
}

object LenientClaudeScanOptions : LenientObjectSerializer<ClaudeScanOptionsDto>(ClaudeScanOptionsDto.serializer())
object LenientCodexScanOptions : LenientObjectSerializer<CodexScanOptionsDto>(CodexScanOptionsDto.serializer())
object LenientAntigravityScanOptions :
    LenientObjectSerializer<AntigravityScanOptionsDto>(AntigravityScanOptionsDto.serializer())
object LenientOpenCodeScanOptions : LenientObjectSerializer<OpenCodeScanOptionsDto>(OpenCodeScanOptionsDto.serializer())
object LenientCopilotScanOptions : LenientObjectSerializer<CopilotScanOptionsDto>(CopilotScanOptionsDto.serializer())
object LenientCursorScanOptions : LenientObjectSerializer<CursorScanOptionsDto>(CursorScanOptionsDto.serializer())
object LenientJunieScanOptions : LenientObjectSerializer<JunieScanOptionsDto>(JunieScanOptionsDto.serializer())

/**
 * The Auto Scan choices a template keeps; every field may be null. The
 * filters are drafts: a GitHub filter may have no labels yet, a Linear one
 * blank fields. Only the chosen agent's options are set.
 */
@Serializable
data class AutoScanTemplateSettingsDto(
    val source: String? = null,
    val cli: String? = null,
    val initial_prompt: String? = null,
    val linear_filter: AutoScanFilterDraftDto? = null,
    val github_filter: GitHubScanFilterDto? = null,
    @Serializable(with = LenientClaudeScanOptions::class)
    val claude_options: ClaudeScanOptionsDto? = null,
    @Serializable(with = LenientCodexScanOptions::class)
    val codex_options: CodexScanOptionsDto? = null,
    @Serializable(with = LenientAntigravityScanOptions::class)
    val antigravity_options: AntigravityScanOptionsDto? = null,
    @Serializable(with = LenientOpenCodeScanOptions::class)
    val opencode_options: OpenCodeScanOptionsDto? = null,
    @Serializable(with = LenientCopilotScanOptions::class)
    val copilot_options: CopilotScanOptionsDto? = null,
    @Serializable(with = LenientCursorScanOptions::class)
    val cursor_options: CursorScanOptionsDto? = null,
    @Serializable(with = LenientJunieScanOptions::class)
    val junie_options: JunieScanOptionsDto? = null,
)

/** A named set of Auto Scan choices saved on the PC. */
@Serializable
data class AutoScanTemplateDto(
    val id: String,
    val name: String,
    val updated_at: String = "",
    val settings: AutoScanTemplateSettingsDto = AutoScanTemplateSettingsDto(),
)

/** `GET`/`POST`/`DELETE /api/autoscan/templates`: every template of the PC, sorted by name. */
@Serializable
data class AutoScanTemplatesResponse(
    val templates: List<AutoScanTemplateDto> = emptyList(),
)

/** `POST /api/autoscan/templates`: a template of the same name (ignoring case) is replaced. */
@Serializable
data class SaveAutoScanTemplateRequest(
    val name: String,
    val settings: AutoScanTemplateSettingsDto,
)
