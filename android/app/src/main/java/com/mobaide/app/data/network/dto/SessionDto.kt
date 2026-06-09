package com.mobaide.app.data.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
enum class SessionKind {
    @SerialName("claude") Claude,
    @SerialName("auto_scan") AutoScan,
    @SerialName("shell") Shell,
}

@Serializable
data class SessionResponse(
    val session_id: String,
    val state: String,
    val created_at: String,
    val pid: Int? = null,
    val command: List<String> = emptyList(),
    val cwd: String? = null,
    val title: String? = null,
    val project_name: String? = null,
    val kind: SessionKind = SessionKind.Claude,
    // Hook-driven attention state (proto-v4): "awaiting_permission" |
    // "turn_finished" | "ended" | null. Stamped on every session snapshot so a
    // reconnecting client recovers the "awaiting you" state after a missed
    // one-shot session.attention event. exit_reason is the human-readable end
    // cause, set when a session ends.
    val attention: String? = null,
    val exit_reason: String? = null,
)

@Serializable
data class CreateSessionRequest(
    val command: List<String>? = null,
    val cwd: String? = null,
    val env: Map<String, String>? = null,
    val rows: Int = 24,
    val cols: Int = 80,
    // When set, command/cwd/env are ignored; the server derives them from the named project.
    val project_name: String? = null,
    val claude_options: ClaudeOptionsModel? = null,
)

@Serializable
data class ScrollbackResponse(
    val session_id: String,
    val lines: List<String>,
    val total: Int,
)
