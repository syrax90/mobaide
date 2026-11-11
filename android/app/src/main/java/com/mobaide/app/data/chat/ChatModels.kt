package com.mobaide.app.data.chat

import androidx.compose.runtime.Immutable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull

object ChatStatus {
    const val STARTING = "starting"
    const val LOADING = "loading"
    const val READY = "ready"
    const val RUNNING = "running"
    const val ERROR = "error"
    const val STOPPED = "stopped"

    private val KNOWN = setOf(STARTING, LOADING, READY, RUNNING, ERROR, STOPPED)

    fun normalize(raw: String?): String = if (raw in KNOWN) raw!! else READY
}

@Immutable
data class ChatAgentInfo(val name: String?, val title: String?, val version: String?)

@Immutable
data class ChatConfigValue(
    val value: String,
    val name: String,
    val description: String?,
    val group: String?,
)

@Immutable
data class ChatConfigOption(
    val id: String,
    val name: String,
    val description: String?,
    val category: String?,
    val isBoolean: Boolean,
    val selectValue: String?,
    val boolValue: Boolean,
    val options: List<ChatConfigValue>,
) {
    fun currentName(): String? = options.firstOrNull { it.value == selectValue }?.name ?: selectValue

    companion object {
        const val CATEGORY_MODE = "mode"
        const val CATEGORY_MODEL = "model"
        const val CATEGORY_THOUGHT = "thought_level"
    }
}

@Immutable
data class ChatSlashCommand(val name: String, val description: String, val hint: String?)

@Immutable
data class ChatCost(val amount: Double, val currency: String)

@Immutable
data class ChatUsage(val used: Long, val size: Long, val cost: ChatCost?)

@Immutable
data class ChatPlanEntry(val content: String, val status: String, val priority: String)

@Immutable
data class ChatQueuedMessage(val id: String, val text: String, val images: Int)

@Immutable
data class ChatPermissionOption(val id: String, val name: String, val kind: String) {
    val isAllow: Boolean get() = kind.startsWith("allow")
    val isReject: Boolean get() = kind.startsWith("reject")
}

@Immutable
data class ChatFormOption(val value: String, val label: String, val description: String?)

enum class ChatFieldType { Select, MultiSelect, Text, Boolean, Number, Integer, Unknown }

@Immutable
data class ChatFormField(
    val key: String,
    val title: String,
    val description: String?,
    val required: Boolean,
    val type: ChatFieldType,
    val options: List<ChatFormOption>,
    val default: JsonElement?,
)

@Immutable
sealed interface ChatRequest {
    val id: String
    val toolCallId: String?
    val title: String

    data class Permission(
        override val id: String,
        override val toolCallId: String?,
        override val title: String,
        val toolKind: String,
        val subtitle: String?,
        val content: List<ChatContentPart>,
        val options: List<ChatPermissionOption>,
    ) : ChatRequest

    data class Form(
        override val id: String,
        override val toolCallId: String?,
        override val title: String,
        val fields: List<ChatFormField>,
    ) : ChatRequest

    /**
     * Sign-in to the coding agent on the PC. [stage] `choose` offers [methods];
     * `waiting` runs one: open [url], enter [userCode] there, or paste the code
     * the page shows into [input].
     */
    data class SignIn(
        override val id: String,
        override val toolCallId: String?,
        override val title: String,
        val text: String,
        val stage: String,
        val methods: List<ChatSignInMethod>,
        val url: String?,
        val userCode: String?,
        val input: ChatSignInInput?,
        val busy: Boolean,
        val error: String?,
    ) : ChatRequest {
        val waiting: Boolean get() = stage == STAGE_WAITING

        companion object {
            const val STAGE_WAITING = "waiting"
        }
    }

    data class Other(
        override val id: String,
        override val toolCallId: String?,
        override val title: String,
    ) : ChatRequest
}

data class ChatSignInMethod(val id: String, val name: String, val description: String)

data class ChatSignInInput(val label: String, val secret: Boolean)

@Immutable
sealed interface ChatContentPart {
    data class Text(val text: String, val truncated: Boolean) : ChatContentPart
    data class Diff(
        val path: String,
        val newFile: Boolean,
        val diff: String,
        val added: Int,
        val removed: Int,
        val truncated: Boolean,
    ) : ChatContentPart
    data class Image(val mime: String, val data: String?) : ChatContentPart
    data class Link(val uri: String, val name: String) : ChatContentPart
    data class Resource(val uri: String, val name: String, val text: String?) : ChatContentPart
    data class Terminal(val terminalId: String) : ChatContentPart
}

@Immutable
data class ChatAttachment(val isImage: Boolean, val label: String)

@Immutable
data class ChatLocation(val path: String, val line: Long?) {
    val label: String get() = if (line != null) "$path:$line" else path
}

object ToolKind {
    private val KNOWN = setOf(
        "read", "edit", "delete", "move", "search", "execute", "think", "fetch", "switch_mode", "other",
    )

    fun normalize(raw: String?): String = if (raw in KNOWN) raw!! else "other"
}

object ToolStatus {
    const val PENDING = "pending"
    const val IN_PROGRESS = "in_progress"
    const val COMPLETED = "completed"
    const val FAILED = "failed"
    const val CANCELLED = "cancelled"
}

@Immutable
sealed interface ChatItem {
    val id: String

    data class User(
        override val id: String,
        val text: String,
        val attachments: List<ChatAttachment>,
    ) : ChatItem

    data class Agent(override val id: String, val text: String) : ChatItem

    data class Thought(override val id: String, val text: String) : ChatItem

    data class Tool(
        override val id: String,
        val title: String,
        val kind: String,
        val status: String,
        val subtitle: String?,
        val locations: List<ChatLocation>,
        val content: List<ChatContentPart>,
        val input: String?,
    ) : ChatItem {
        val diffAdded: Int get() = content.sumOf { (it as? ChatContentPart.Diff)?.added ?: 0 }
        val diffRemoved: Int get() = content.sumOf { (it as? ChatContentPart.Diff)?.removed ?: 0 }
        val hasDiff: Boolean get() = content.any { it is ChatContentPart.Diff }
    }

    data class Notice(
        override val id: String,
        val severity: String,
        val title: String,
        val text: String,
    ) : ChatItem

    data class Compaction(override val id: String, val status: String, val text: String) : ChatItem

    data class TurnEnd(override val id: String, val stopReason: String, val text: String) : ChatItem

    data class Error(override val id: String, val text: String) : ChatItem
}

/** The item with [extra] appended to its text, or null when the type carries no text. */
fun ChatItem.appendText(extra: String): ChatItem? = when (this) {
    is ChatItem.User -> copy(text = text + extra)
    is ChatItem.Agent -> copy(text = text + extra)
    is ChatItem.Thought -> copy(text = text + extra)
    is ChatItem.Notice -> copy(text = text + extra)
    is ChatItem.Compaction -> copy(text = text + extra)
    is ChatItem.TurnEnd -> copy(text = text + extra)
    is ChatItem.Error -> copy(text = text + extra)
    is ChatItem.Tool -> null
}

@Immutable
data class ChatState(
    val status: String = ChatStatus.STARTING,
    val error: String? = null,
    val agent: ChatAgentInfo? = null,
    val title: String? = null,
    val cwd: String? = null,
    val acpSessionId: String? = null,
    val config: List<ChatConfigOption> = emptyList(),
    val commands: List<ChatSlashCommand> = emptyList(),
    val usage: ChatUsage? = null,
    val plan: List<ChatPlanEntry> = emptyList(),
    val requests: List<ChatRequest> = emptyList(),
    val queue: List<ChatQueuedMessage> = emptyList(),
    val promptImages: Boolean = false,
    val turnStartedAt: String? = null,
    val cancelling: Boolean = false,
    // The agent takes a message during a running turn ("Send now" on queued messages).
    val steering: Boolean = false,
)

/** Lenient decoding of the agent's JSON: a malformed field never drops its whole frame. */
internal object ChatJson {

    fun state(obj: JsonObject): ChatState = ChatState(
        status = ChatStatus.normalize(obj.str("status")),
        error = obj.str("error"),
        agent = obj.obj("agent")?.let {
            ChatAgentInfo(name = it.str("name"), title = it.str("title"), version = it.str("version"))
        },
        title = obj.str("title"),
        cwd = obj.str("cwd"),
        acpSessionId = obj.str("acp_session_id"),
        config = obj.objects("config").mapNotNull(::configOption),
        commands = obj.objects("commands").mapNotNull { cmd ->
            val name = cmd.str("name")?.takeIf { it.isNotBlank() } ?: return@mapNotNull null
            ChatSlashCommand(name = name, description = cmd.str("description").orEmpty(), hint = cmd.str("hint"))
        },
        usage = obj.obj("usage")?.let { usage ->
            val used = usage.long("used") ?: return@let null
            val size = usage.long("size") ?: return@let null
            ChatUsage(
                used = used,
                size = size,
                cost = usage.obj("cost")?.let { cost ->
                    val amount = cost.double("amount") ?: return@let null
                    ChatCost(amount, cost.str("currency") ?: "USD")
                },
            )
        },
        plan = obj.objects("plan").mapNotNull { entry ->
            val content = entry.str("content") ?: return@mapNotNull null
            ChatPlanEntry(
                content = content,
                status = entry.str("status") ?: "pending",
                priority = entry.str("priority") ?: "medium",
            )
        },
        requests = obj.objects("requests").mapNotNull(::request),
        queue = obj.objects("queue").mapNotNull { q ->
            val id = q.str("id") ?: return@mapNotNull null
            ChatQueuedMessage(id = id, text = q.str("text").orEmpty(), images = q.long("images")?.toInt() ?: 0)
        },
        promptImages = obj.obj("prompt_caps")?.bool("image") == true,
        turnStartedAt = obj.str("turn_started_at"),
        cancelling = obj.bool("cancelling") == true,
        steering = obj.bool("steering") == true,
    )

    private fun configOption(obj: JsonObject): ChatConfigOption? {
        val id = obj.str("id") ?: return null
        val type = obj.str("type")
        if (type != "select" && type != "boolean") return null
        val value = obj["value"] as? JsonPrimitive
        return ChatConfigOption(
            id = id,
            name = obj.str("name") ?: id,
            description = obj.str("description"),
            category = obj.str("category"),
            isBoolean = type == "boolean",
            selectValue = value?.takeIf { it.isString }?.content,
            boolValue = value?.takeIf { !it.isString }?.booleanOrNull == true,
            options = obj.objects("options").mapNotNull { opt ->
                val v = opt.str("value") ?: return@mapNotNull null
                ChatConfigValue(
                    value = v,
                    name = opt.str("name") ?: v,
                    description = opt.str("description"),
                    group = opt.str("group"),
                )
            },
        )
    }

    private fun request(obj: JsonObject): ChatRequest? {
        val id = obj.str("id") ?: return null
        val toolCallId = obj.str("tool_call_id")
        val title = obj.str("title").orEmpty()
        return when (obj.str("kind")) {
            "permission" -> ChatRequest.Permission(
                id = id,
                toolCallId = toolCallId,
                title = title,
                toolKind = ToolKind.normalize(obj.str("tool_kind")),
                subtitle = obj.str("subtitle"),
                content = obj.objects("content").mapNotNull(::contentPart),
                options = obj.objects("options").mapNotNull { opt ->
                    val optId = opt.str("id") ?: return@mapNotNull null
                    ChatPermissionOption(id = optId, name = opt.str("name") ?: optId, kind = opt.str("kind").orEmpty())
                },
            )
            "form" -> ChatRequest.Form(
                id = id,
                toolCallId = toolCallId,
                title = title,
                fields = obj.objects("fields").mapNotNull(::formField),
            )
            "sign_in" -> ChatRequest.SignIn(
                id = id,
                toolCallId = toolCallId,
                title = title,
                text = obj.str("text").orEmpty(),
                stage = obj.str("stage") ?: "choose",
                methods = obj.objects("methods").mapNotNull { method ->
                    val methodId = method.str("id") ?: return@mapNotNull null
                    ChatSignInMethod(
                        id = methodId,
                        name = method.str("name") ?: methodId,
                        description = method.str("description").orEmpty(),
                    )
                },
                url = obj.str("url")?.takeIf { it.startsWith("https://") },
                userCode = obj.str("user_code"),
                input = obj.obj("input")?.let { input ->
                    ChatSignInInput(label = input.str("label").orEmpty(), secret = input.bool("secret") == true)
                },
                busy = obj.bool("busy") == true,
                error = obj.str("error"),
            )
            else -> ChatRequest.Other(id = id, toolCallId = toolCallId, title = title)
        }
    }

    private fun formField(obj: JsonObject): ChatFormField? {
        val key = obj.str("key") ?: return null
        return ChatFormField(
            key = key,
            title = obj.str("title") ?: key,
            description = obj.str("description"),
            required = obj.bool("required") == true,
            type = when (obj.str("type")) {
                "select" -> ChatFieldType.Select
                "multiselect" -> ChatFieldType.MultiSelect
                "text" -> ChatFieldType.Text
                "boolean" -> ChatFieldType.Boolean
                "number" -> ChatFieldType.Number
                "integer" -> ChatFieldType.Integer
                else -> ChatFieldType.Unknown
            },
            options = obj.objects("options").mapNotNull { opt ->
                val value = opt.str("value") ?: return@mapNotNull null
                ChatFormOption(value = value, label = opt.str("label") ?: value, description = opt.str("description"))
            },
            default = obj["default"]?.takeIf { it !is JsonNull },
        )
    }

    fun contentPart(obj: JsonObject): ChatContentPart? = when (obj.str("type")) {
        "text" -> ChatContentPart.Text(text = obj.str("text").orEmpty(), truncated = obj.bool("truncated") == true)
        "diff" -> ChatContentPart.Diff(
            path = obj.str("path").orEmpty(),
            newFile = obj.bool("new_file") == true,
            diff = obj.str("diff").orEmpty(),
            added = obj.long("added")?.toInt() ?: 0,
            removed = obj.long("removed")?.toInt() ?: 0,
            truncated = obj.bool("truncated") == true,
        )
        "image" -> ChatContentPart.Image(mime = obj.str("mime").orEmpty(), data = obj.str("data"))
        "link" -> obj.str("uri")?.let { ChatContentPart.Link(uri = it, name = obj.str("name") ?: it) }
        "resource" -> obj.str("uri")?.let {
            ChatContentPart.Resource(uri = it, name = obj.str("name") ?: it, text = obj.str("text"))
        }
        "terminal" -> ChatContentPart.Terminal(terminalId = obj.str("terminal_id").orEmpty())
        else -> null
    }

    fun item(obj: JsonObject): ChatItem? {
        val id = obj.str("id") ?: return null
        return when (obj.str("type")) {
            "user" -> ChatItem.User(
                id = id,
                text = obj.str("text").orEmpty(),
                attachments = obj.objects("attachments").mapNotNull { att ->
                    when (att.str("kind")) {
                        "image" -> ChatAttachment(isImage = true, label = "Image")
                        "file" -> ChatAttachment(isImage = false, label = att.str("name") ?: "File")
                        else -> null
                    }
                },
            )
            "agent" -> ChatItem.Agent(id = id, text = obj.str("text").orEmpty())
            "thought" -> ChatItem.Thought(id = id, text = obj.str("text").orEmpty())
            "tool" -> ChatItem.Tool(
                id = id,
                title = obj.str("title").orEmpty(),
                kind = ToolKind.normalize(obj.str("kind")),
                status = obj.str("status") ?: ToolStatus.PENDING,
                subtitle = obj.str("subtitle"),
                locations = obj.objects("locations").mapNotNull { loc ->
                    val path = loc.str("path") ?: return@mapNotNull null
                    ChatLocation(path = path, line = loc.long("line"))
                },
                content = obj.objects("content").mapNotNull(::contentPart),
                input = obj.str("input"),
            )
            "notice" -> ChatItem.Notice(
                id = id,
                severity = obj.str("severity") ?: "info",
                title = obj.str("title").orEmpty(),
                text = obj.str("text").orEmpty(),
            )
            "compaction" -> ChatItem.Compaction(
                id = id,
                status = obj.str("status") ?: "completed",
                text = obj.str("text").orEmpty(),
            )
            "turn_end" -> ChatItem.TurnEnd(
                id = id,
                stopReason = obj.str("stop_reason").orEmpty(),
                text = obj.str("text").orEmpty(),
            )
            "error" -> ChatItem.Error(id = id, text = obj.str("text").orEmpty())
            else -> null
        }
    }
}

internal fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.takeIf { it.isString }?.content

internal fun JsonObject.bool(key: String): Boolean? =
    (this[key] as? JsonPrimitive)?.takeIf { !it.isString }?.booleanOrNull

internal fun JsonObject.long(key: String): Long? =
    (this[key] as? JsonPrimitive)?.takeIf { !it.isString }?.let { it.longOrNull ?: it.doubleOrNull?.toLong() }

internal fun JsonObject.double(key: String): Double? =
    (this[key] as? JsonPrimitive)?.takeIf { !it.isString }?.doubleOrNull

internal fun JsonObject.obj(key: String): JsonObject? = this[key] as? JsonObject

internal fun JsonObject.objects(key: String): List<JsonObject> =
    (this[key] as? JsonArray)?.mapNotNull { it as? JsonObject }.orEmpty()
