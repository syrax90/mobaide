package com.mobaide.app.data.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
enum class SessionKind {
    @SerialName("claude") Claude,
    @SerialName("codex") Codex,
    @SerialName("antigravity") Antigravity,
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
    // Attention state (proto-v4): "awaiting_permission" |
    // "awaiting_os_permission" | "turn_finished" | "ended" | null.
    // "awaiting_os_permission" is the agent's startup-stall heuristic (the
    // session is most likely blocked on a macOS file-access prompt on the
    // host); the rest are Claude Code hook-driven. Stamped on every session
    // snapshot so a reconnecting client recovers the "awaiting you" state after
    // a missed one-shot session.attention event. exit_reason is the
    // human-readable end cause, set when a session ends.
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
    // Which managed CLI to launch: "claude" | "codex" | "antigravity". Null for Terminal/shell
    // sessions; when null the server infers the kind from whichever options block is present.
    val cli: String? = null,
    val claude_options: ClaudeOptionsModel? = null,
    val codex_options: CodexOptionsModel? = null,
    val antigravity_options: AntigravityOptionsModel? = null,
    // Terminal-mode worktree: create a git worktree for a shell session (no CLI options)
    // on a stored git project and spawn the shell inside it. Managed Claude/Codex sessions
    // instead carry worktree intent inside their own options block.
    val worktree: Boolean = false,
    val branch_name: String? = null,
)

@Serializable
data class ScrollbackResponse(
    val session_id: String,
    val lines: List<String>,
    val total: Int,
)
