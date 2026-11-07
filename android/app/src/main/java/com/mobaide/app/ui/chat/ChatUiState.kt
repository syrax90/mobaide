package com.mobaide.app.ui.chat

import androidx.compose.runtime.Immutable
import androidx.compose.ui.graphics.ImageBitmap
import com.mobaide.app.data.chat.ChatConfigOption
import com.mobaide.app.data.chat.ChatFieldType
import com.mobaide.app.data.chat.ChatFormField
import com.mobaide.app.data.chat.ChatImagePayload
import com.mobaide.app.data.chat.ChatItem
import com.mobaide.app.data.chat.ChatRequest
import com.mobaide.app.data.chat.ChatSlashCommand
import com.mobaide.app.data.chat.ChatState
import com.mobaide.app.data.chat.ChatStatus
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull

@Immutable
data class ChatComposerImage(val id: Long, val payload: ChatImagePayload, val thumbnail: ImageBitmap)

/** A form answer while the user edits it. */
@Immutable
sealed interface ChatFormValue {
    data class Text(val value: String) : ChatFormValue
    data class Toggle(val value: Boolean) : ChatFormValue
    data class Choice(val value: String?) : ChatFormValue
    data class Choices(val values: Set<String>) : ChatFormValue
}

@Immutable
data class ChatUiState(
    /** False until the first snapshot (or a cached copy) is on screen. */
    val loaded: Boolean = false,
    val items: List<ChatItem> = emptyList(),
    val truncated: Boolean = false,
    val state: ChatState = ChatState(),
    /** [ChatState.config] with the values this client just set but the agent has not confirmed. */
    val config: List<ChatConfigOption> = emptyList(),
    val live: Boolean = false,
    /** The session itself is gone (`'s'` stopped): nothing can be sent any more. */
    val sessionClosed: Boolean = false,
    /** Request id → the option id or form action whose answer is in flight. */
    val answering: Map<String, String> = emptyMap(),
    val requestErrors: Map<String, String> = emptyMap(),
    val formDrafts: Map<String, Map<String, ChatFormValue>> = emptyMap(),
    val images: List<ChatComposerImage> = emptyList(),
    val imagesLoading: Boolean = false,
    /** A `restart` this client sent has no result yet. */
    val restarting: Boolean = false,
) {
    val status: String get() = if (sessionClosed) ChatStatus.STOPPED else state.status

    val running: Boolean get() = !sessionClosed && state.status == ChatStatus.RUNNING

    /** Whether commands can reach a live agent right now. */
    val canAct: Boolean
        get() = live && !sessionClosed && state.status != ChatStatus.ERROR && state.status != ChatStatus.STOPPED

    val canRestart: Boolean
        get() = live && !sessionClosed && !restarting && state.status == ChatStatus.ERROR

    /** The item still receiving text: the last one, while a turn runs. */
    val streamingItemId: String?
        get() = if (running) items.lastOrNull()?.takeIf { it is ChatItem.Agent || it is ChatItem.Thought }?.id else null

    val waitingToolIds: Set<String>
        get() = state.requests.mapNotNullTo(HashSet()) { it.toolCallId }
}

/** Commands whose name starts with, then contains, what follows the `/`; null when no list applies. */
fun slashSuggestions(text: String, commands: List<ChatSlashCommand>): List<ChatSlashCommand>? {
    if (!text.startsWith("/") || text.any { it.isWhitespace() } || commands.isEmpty()) return null
    val query = text.substring(1).lowercase()
    val prefix = commands.filter { it.name.lowercase().startsWith(query) }
    val contains = commands.filter { it.name.lowercase().contains(query) && it !in prefix }
    return (prefix + contains).takeIf { it.isNotEmpty() }
}

fun initialFormDraft(fields: List<ChatFormField>): Map<String, ChatFormValue> = fields.associate { field ->
    val default = field.default
    val primitive = default as? JsonPrimitive
    field.key to when (field.type) {
        ChatFieldType.Select -> ChatFormValue.Choice(primitive?.takeIf { it.isString }?.content)
        ChatFieldType.MultiSelect -> ChatFormValue.Choices(
            (default as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.takeIf { p -> p.isString }?.content }?.toSet()
                ?: emptySet(),
        )
        ChatFieldType.Boolean -> ChatFormValue.Toggle(primitive?.takeIf { !it.isString }?.booleanOrNull == true)
        ChatFieldType.Number, ChatFieldType.Integer -> ChatFormValue.Text(
            primitive?.takeIf { !it.isString }?.let { p ->
                p.longOrNull?.toString() ?: p.doubleOrNull?.toString()
            }.orEmpty(),
        )
        ChatFieldType.Text, ChatFieldType.Unknown -> ChatFormValue.Text(primitive?.takeIf { it.isString }?.content.orEmpty())
    }
}

/**
 * The `values` of an accepted form, or null while a required field is empty or a
 * number does not parse. Empty optional fields are left out.
 */
fun formValues(request: ChatRequest.Form, draft: Map<String, ChatFormValue>): JsonObject? {
    val draftOrDefault = initialFormDraft(request.fields) + draft
    val values = LinkedHashMap<String, kotlinx.serialization.json.JsonElement>()
    for (field in request.fields) {
        val value = draftOrDefault[field.key]
        when (field.type) {
            ChatFieldType.Select -> {
                val choice = (value as? ChatFormValue.Choice)?.value
                if (choice != null) values[field.key] = JsonPrimitive(choice) else if (field.required) return null
            }
            ChatFieldType.MultiSelect -> {
                val chosen = (value as? ChatFormValue.Choices)?.values.orEmpty()
                if (chosen.isNotEmpty()) {
                    val ordered = field.options.map { it.value }.filter { it in chosen }
                    values[field.key] = JsonArray(ordered.map(::JsonPrimitive))
                } else if (field.required) {
                    return null
                }
            }
            ChatFieldType.Boolean -> values[field.key] = JsonPrimitive((value as? ChatFormValue.Toggle)?.value == true)
            ChatFieldType.Number, ChatFieldType.Integer -> {
                val raw = (value as? ChatFormValue.Text)?.value?.trim().orEmpty()
                if (raw.isEmpty()) {
                    if (field.required) return null
                } else {
                    val number: Number = if (field.type == ChatFieldType.Integer) {
                        raw.toLongOrNull() ?: return null
                    } else {
                        raw.toDoubleOrNull() ?: return null
                    }
                    values[field.key] = JsonPrimitive(number)
                }
            }
            ChatFieldType.Text, ChatFieldType.Unknown -> {
                val raw = (value as? ChatFormValue.Text)?.value.orEmpty()
                if (raw.isNotBlank()) values[field.key] = JsonPrimitive(raw) else if (field.required) return null
            }
        }
    }
    return buildJsonObject { values.forEach { (k, v) -> put(k, v) } }
}
