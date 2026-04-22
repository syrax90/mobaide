package com.claudemobile.app.data.network

import com.claudemobile.app.data.events.ScanPayloadParser
import com.claudemobile.app.data.events.ScanProgressPayload
import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.put

/**
 * Connects to /ws/autoscan and emits structured [Frame]s.
 *
 * Sends an initial config frame containing optional `subscribe` and
 * `resume_from_seq` immediately after the socket opens.
 */
class AutoScanWsClient(
    private val webSocketManager: WebSocketManager,
) {
    sealed class Frame {
        data class Snapshot(
            val scans: List<AutoScanInstanceDto>,
            val events: Map<String, List<ParsedEvent>>,
        ) : Frame()
        object CaughtUp : Frame()
        data class Event(val event: ParsedEvent) : Frame()
        data class Disconnected(val cause: Throwable?) : Frame()
        object Connected : Frame()
    }

    data class ParsedEvent(
        val projectName: String,
        val seq: Long,
        val ts: String,
        val type: String,
        val payload: ScanProgressPayload,
    )

    private val json = Json { ignoreUnknownKeys = true }

    fun connect(
        subscribe: List<String> = emptyList(),
        resumeFromSeq: Map<String, Long> = emptyMap(),
    ): Flow<Frame> = callbackFlow {
        val conn = webSocketManager.createConnection("ws/autoscan")
        val collectorJob = launch {
            conn.events.collect { ev ->
                when (ev) {
                    is WsEvent.Connected -> {
                        val cfg = buildJsonObject {
                            put("subscribe", json.encodeToJsonElement(subscribe))
                            val rfs = buildJsonObject {
                                resumeFromSeq.forEach { (k, v) -> put(k, v) }
                            }
                            put("resume_from_seq", rfs)
                        }
                        conn.send(json.encodeToString(JsonObject.serializer(), cfg))
                        trySend(Frame.Connected)
                    }
                    is WsEvent.Message -> {
                        val frame = parseFrame(ev.text)
                        if (frame != null) trySend(frame)
                    }
                    is WsEvent.Disconnected -> trySend(Frame.Disconnected(null))
                    is WsEvent.Error -> trySend(Frame.Disconnected(ev.throwable))
                    is WsEvent.Binary -> Unit
                }
            }
        }
        conn.connect()
        awaitClose {
            collectorJob.cancel()
            conn.disconnect()
        }
    }

    private fun parseFrame(text: String): Frame? {
        return try {
            val obj = json.parseToJsonElement(text).jsonObject
            when (obj["type"]?.jsonPrimitive?.contentOrNull) {
                "snapshot" -> {
                    val data = obj["data"]?.jsonObject ?: return null
                    val scansEl = data["scans"]?.jsonArray.orEmpty()
                    val scans = scansEl.map {
                        json.decodeFromJsonElement(AutoScanInstanceDto.serializer(), it)
                    }
                    val eventsObj = data["events"]?.jsonObject
                    val events: MutableMap<String, List<ParsedEvent>> = mutableMapOf()
                    eventsObj?.forEach { (proj, arr) ->
                        events[proj] = arr.jsonArray.mapNotNull { evEl ->
                            parseEventDict(proj, evEl.jsonObject)
                        }
                    }
                    Frame.Snapshot(scans, events)
                }
                "caught_up" -> Frame.CaughtUp
                "event" -> {
                    val proj = obj["project_name"]?.jsonPrimitive?.contentOrNull.orEmpty()
                    val evObj = obj["event"]?.jsonObject ?: return null
                    val parsed = parseEventDict(proj, evObj) ?: return null
                    Frame.Event(parsed)
                }
                else -> null
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun parseEventDict(projectName: String, ev: JsonObject): ParsedEvent? {
        return try {
            val seq = ev["seq"]?.jsonPrimitive?.long ?: 0L
            val ts = ev["ts"]?.jsonPrimitive?.contentOrNull.orEmpty()
            val type = ev["type"]?.jsonPrimitive?.contentOrNull.orEmpty()
            val payload = ev["payload"]?.jsonObject ?: JsonObject(emptyMap())
            ParsedEvent(
                projectName = projectName,
                seq = seq,
                ts = ts,
                type = type,
                payload = ScanPayloadParser.parse(type, payload),
            )
        } catch (_: Exception) {
            null
        }
    }
}

private fun List<kotlinx.serialization.json.JsonElement>?.orEmpty():
    List<kotlinx.serialization.json.JsonElement> = this ?: emptyList()
