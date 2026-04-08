package com.claudemobile.app.data.events

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive

/** Parses the `payload` JSON of an autoscan event into a typed [ScanProgressPayload]. */
object ScanPayloadParser {
    private fun JsonObject.str(key: String): String =
        get(key)?.jsonPrimitive?.contentOrNull.orEmpty()
    private fun JsonObject.strOrNull(key: String): String? =
        get(key)?.jsonPrimitive?.contentOrNull
    private fun JsonObject.intOr(key: String, default: Int = 0): Int =
        get(key)?.jsonPrimitive?.intOrNull ?: default
    private fun JsonObject.intOrNull(key: String): Int? =
        get(key)?.jsonPrimitive?.intOrNull
    private fun JsonObject.boolOr(key: String, default: Boolean = false): Boolean =
        get(key)?.jsonPrimitive?.booleanOrNull ?: default

    fun parse(type: String, payload: JsonObject): ScanProgressPayload = when (type) {
        "phase" -> ScanProgressPayload.Phase(
            phase = payload.str("phase"),
            cycleNumber = payload.intOr("cycle_number"),
            nextScanAt = payload.strOrNull("next_scan_at"),
        )
        "issue" -> ScanProgressPayload.Issue(
            identifier = payload.str("identifier"),
            title = payload.str("title"),
            step = payload.str("step"),
            status = payload.str("status"),
            error = payload.strOrNull("error"),
        )
        "worktree" -> ScanProgressPayload.Worktree(
            identifier = payload.str("identifier"),
            path = payload.str("path"),
        )
        "session" -> ScanProgressPayload.Session(
            identifier = payload.str("identifier"),
            sessionId = payload.str("session_id"),
            pid = payload.intOrNull("pid"),
        )
        "log" -> ScanProgressPayload.Log(
            line = payload.str("line"),
            level = payload.strOrNull("level") ?: "info",
            ansi = payload.boolOr("ansi"),
        )
        else -> ScanProgressPayload.Unknown(type)
    }
}
