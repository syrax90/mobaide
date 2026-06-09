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
    // Terminal-mode worktree: create a git worktree for a shell session (claude_options null)
    // on a stored git project and spawn the shell inside it.
    val worktree: Boolean = false,
    val branch_name: String? = null,
)

@Serializable
data class ScrollbackResponse(
    val session_id: String,
    val lines: List<String>,
    val total: Int,
)
