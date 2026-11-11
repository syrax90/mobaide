package com.mobaide.app.data.chat

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okio.Buffer
import okio.ByteString
import okio.ByteString.Companion.encodeUtf8

/** One timeline change inside an `events` frame. Every kind carries a seq, known or not. */
sealed interface ChatEvent {
    val seq: Long

    /** [item] is null for an item type this build does not know: skipped, but its seq counts. */
    data class Upsert(override val seq: Long, val item: ChatItem?) : ChatEvent
    data class Append(override val seq: Long, val id: String, val text: String) : ChatEvent
    data class Remove(override val seq: Long, val id: String) : ChatEvent
    data class StatePatch(override val seq: Long, val patch: JsonObject) : ChatEvent
    data class Unknown(override val seq: Long) : ChatEvent
}

sealed interface ChatFrame {
    data class Snapshot(
        val epoch: String,
        val seq: Long,
        val truncated: Boolean,
        val state: JsonObject,
        val items: List<ChatItem>,
    ) : ChatFrame

    data class Events(val epoch: String, val seq: Long, val events: List<ChatEvent>) : ChatFrame

    data class Result(val id: String, val ok: Boolean, val data: JsonObject?, val error: String?) : ChatFrame
}

/** An image ready for a `prompt` command: base64 without a `data:` prefix. */
data class ChatImagePayload(val mime: String, val data: String)

/**
 * Chat sessions share the per-session socket with terminal sessions: `'c'` frames
 * carry one JSON command to the agent, `'a'` frames one JSON timeline frame back.
 */
object ChatProtocol {
    const val TYPE_COMMAND: Byte = 'c'.code.toByte()
    const val TYPE_FRAME: Byte = 'a'.code.toByte()

    private val json = Json { ignoreUnknownKeys = true }

    fun encode(command: JsonObject): ByteString =
        Buffer().writeByte(TYPE_COMMAND.toInt()).write(command.toString().encodeUtf8()).readByteString()

    fun sync(id: String, epoch: String?, afterSeq: Long?): JsonObject = buildJsonObject {
        put("id", id)
        put("op", "sync")
        if (epoch != null && afterSeq != null) {
            put("epoch", epoch)
            put("after_seq", afterSeq)
        }
    }

    fun prompt(id: String, text: String, images: List<ChatImagePayload>): JsonObject = buildJsonObject {
        put("id", id)
        put("op", "prompt")
        put("text", text)
        if (images.isNotEmpty()) {
            put(
                "images",
                buildJsonArray {
                    images.forEach { image ->
                        add(buildJsonObject {
                            put("mime", image.mime)
                            put("data", image.data)
                        })
                    }
                },
            )
        }
    }

    fun cancel(id: String): JsonObject = op(id, "cancel")

    fun restart(id: String): JsonObject = op(id, "restart")

    fun setConfig(id: String, configId: String, value: JsonPrimitive): JsonObject = buildJsonObject {
        put("id", id)
        put("op", "set_config")
        put("config_id", configId)
        put("value", value)
    }

    fun respondPermission(id: String, requestId: String, optionId: String): JsonObject = buildJsonObject {
        put("id", id)
        put("op", "respond")
        put("request_id", requestId)
        put("option_id", optionId)
    }

    fun respondForm(id: String, requestId: String, action: String, values: JsonObject?): JsonObject =
        buildJsonObject {
            put("id", id)
            put("op", "respond")
            put("request_id", requestId)
            put("action", action)
            if (values != null) put("values", values)
        }

    fun dequeue(id: String, queueId: String): JsonObject = buildJsonObject {
        put("id", id)
        put("op", "dequeue")
        put("queue_id", queueId)
    }

    /** Send a queued message into the running turn now. */
    fun steer(id: String, queueId: String): JsonObject = buildJsonObject {
        put("id", id)
        put("op", "steer")
        put("queue_id", queueId)
    }

    fun signInStart(id: String, method: String): JsonObject = buildJsonObject {
        put("id", id)
        put("op", "sign_in")
        put("action", "start")
        put("method", method)
    }

    fun signInSubmit(id: String, value: String): JsonObject = buildJsonObject {
        put("id", id)
        put("op", "sign_in")
        put("action", "submit")
        put("value", value)
    }

    fun signInCancel(id: String): JsonObject = buildJsonObject {
        put("id", id)
        put("op", "sign_in")
        put("action", "cancel")
    }

    private fun op(id: String, op: String): JsonObject = buildJsonObject {
        put("id", id)
        put("op", op)
    }

    /** The `'a'` frame in [frame], or null for any other type byte or an unreadable body. */
    fun decode(frame: ByteString): ChatFrame? {
        if (frame.size < 2 || frame[0] != TYPE_FRAME) return null
        val root = runCatching { json.parseToJsonElement(frame.substring(1).utf8()) }.getOrNull()
            as? JsonObject ?: return null
        return decodeFrame(root)
    }

    fun decodeFrame(root: JsonObject): ChatFrame? = when (root.str("t")) {
        "snapshot" -> {
            val epoch = root.str("epoch")
            val seq = root.long("seq")
            if (epoch == null || seq == null) {
                null
            } else {
                ChatFrame.Snapshot(
                    epoch = epoch,
                    seq = seq,
                    truncated = root.bool("truncated") == true,
                    state = root.obj("state") ?: JsonObject(emptyMap()),
                    items = root.objects("items").mapNotNull(ChatJson::item),
                )
            }
        }
        "events" -> {
            val epoch = root.str("epoch")
            val seq = root.long("seq")
            if (epoch == null || seq == null) {
                null
            } else {
                ChatFrame.Events(epoch = epoch, seq = seq, events = root.objects("events").mapNotNull(::event))
            }
        }
        "result" -> root.str("id")?.let { id ->
            ChatFrame.Result(
                id = id,
                ok = root.bool("ok") == true,
                data = root.obj("data"),
                error = root.str("error"),
            )
        }
        else -> null
    }

    private fun event(obj: JsonObject): ChatEvent? {
        val seq = obj.long("seq") ?: return null
        return when (obj.str("type")) {
            "item" -> ChatEvent.Upsert(seq, obj.obj("item")?.let(ChatJson::item))
            "append" -> {
                val id = obj.str("id")
                if (id == null) ChatEvent.Unknown(seq) else ChatEvent.Append(seq, id, obj.str("text").orEmpty())
            }
            "remove" -> obj.str("id")?.let { ChatEvent.Remove(seq, it) } ?: ChatEvent.Unknown(seq)
            "state" -> ChatEvent.StatePatch(seq, obj.obj("patch") ?: JsonObject(emptyMap()))
            else -> ChatEvent.Unknown(seq)
        }
    }
}
