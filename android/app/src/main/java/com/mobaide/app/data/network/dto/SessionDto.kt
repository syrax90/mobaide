package com.mobaide.app.data.network.dto

import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder

/**
 * Open session-kind vocabulary (proto-v7). The built-in kinds are singletons;
 * any other wire string — account-scoped custom agent types (`custom_<12hex>`)
 * or kinds added by a newer server — is preserved verbatim in [Custom] instead
 * of being coerced to Claude, so per-kind behavior (labels, key bars, shortcut
 * scoping) never mislabels a non-Claude session.
 */
@Serializable(with = SessionKindSerializer::class)
sealed interface SessionKind {
    data object Claude : SessionKind
    data object Codex : SessionKind
    data object Antigravity : SessionKind
    data object OpenCode : SessionKind
    data object Copilot : SessionKind
    data object AutoScan : SessionKind
    data object Shell : SessionKind

    /** Any kind string this build does not recognize, held verbatim. */
    data class Custom(val raw: String) : SessionKind
}

/**
 * The wire value of each kind, used as a stable per-kind storage key suffix
 * (shortcut buckets, MRU recents). The exhaustive when forces an entry for
 * every new built-in kind at compile time; [SessionKind.Custom] passes its
 * raw wire string through, which keeps custom buckets rename-stable (the id,
 * not the display name, is in the key).
 */
val SessionKind.wireName: String
    get() = when (this) {
        SessionKind.Claude -> "claude"
        SessionKind.Codex -> "codex"
        SessionKind.Antigravity -> "antigravity"
        SessionKind.OpenCode -> "opencode"
        SessionKind.Copilot -> "copilot"
        SessionKind.AutoScan -> "auto_scan"
        SessionKind.Shell -> "shell"
        is SessionKind.Custom -> raw
    }

/**
 * Hand-written serializer with a [PrimitiveKind.STRING] descriptor — never an
 * ENUM-kind descriptor: `Json { coerceInputValues = true }` performs its
 * unknown-value-to-default coercion *before* invoking a custom deserializer
 * when the descriptor kind is ENUM (kotlinx.serialization#1947), which would
 * silently re-introduce the unknown-kind→Claude mislabel this type exists to
 * remove. With a STRING descriptor the flag is inert for non-null values,
 * while an explicit `"kind": null` still coerces to the property default
 * (null-coercion keys on the property's nullability, not the descriptor).
 */
object SessionKindSerializer : KSerializer<SessionKind> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("SessionKind", PrimitiveKind.STRING)

    private val known = mapOf(
        "claude" to SessionKind.Claude,
        "codex" to SessionKind.Codex,
        "antigravity" to SessionKind.Antigravity,
        "opencode" to SessionKind.OpenCode,
        "copilot" to SessionKind.Copilot,
        "auto_scan" to SessionKind.AutoScan,
        "shell" to SessionKind.Shell,
    )

    override fun deserialize(decoder: Decoder): SessionKind {
        val raw = decoder.decodeString()
        return known[raw] ?: SessionKind.Custom(raw)
    }

    override fun serialize(encoder: Encoder, value: SessionKind) {
        encoder.encodeString(value.wireName)
    }
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
    // Display-name snapshot of a custom agent type at session-creation time
    // (proto-v7); null for built-in kinds. Survives rename/delete of the type,
    // so lists keep a meaningful label without a live registry lookup.
    val kind_label: String? = null,
    // Attention state (proto-v4): "awaiting_permission" |
    // "awaiting_os_permission" | "turn_finished" | "turn_failed" | "ended" | null.
    // "awaiting_os_permission" is the agent's startup-stall heuristic (the
    // session is most likely blocked on a macOS file-access prompt on the
    // host); the rest come from Claude Code hooks, CLI status reports or the
    // chat session. Stamped on every session snapshot so a reconnecting
    // client recovers the "awaiting you" state after a missed one-shot
    // session.attention event. exit_reason is the
    // human-readable end cause, set when a session ends.
    val attention: String? = null,
    val exit_reason: String? = null,
    // "chat" opens the structured chat screen; absent or anything else is the PTY terminal.
    val ui: String? = null,
    // "working" while the agent is mid-turn, "waiting" while it is parked on
    // background work, "idle" between turns, null when the agent cannot tell
    // (Codex, Antigravity and Terminal PTY sessions).
    val activity: String? = null,
) {
    val isChat: Boolean get() = ui == UI_CHAT

    companion object {
        const val UI_CHAT = "chat"
        const val UI_TERMINAL = "terminal"
    }
}

@Serializable
data class CreateSessionRequest(
    val command: List<String>? = null,
    val cwd: String? = null,
    val env: Map<String, String>? = null,
    val rows: Int = 24,
    val cols: Int = 80,
    // When set, command/cwd/env are ignored; the server derives them from the named project.
    val project_name: String? = null,
    // Which managed CLI to launch: "claude" | "codex" | "antigravity" | "opencode" | "copilot", or "custom_<12hex>"
    // for an account-scoped custom agent type (proto-v7; the relay resolves the stored
    // command — the client never sends it). Null for Terminal/shell sessions; when null
    // the server infers the kind from whichever options block is present.
    val cli: String? = null,
    val claude_options: ClaudeOptionsModel? = null,
    val codex_options: CodexOptionsModel? = null,
    val antigravity_options: AntigravityOptionsModel? = null,
    val opencode_options: OpenCodeOptionsModel? = null,
    val copilot_options: CopilotOptionsModel? = null,
    // Terminal-mode worktree: create a git worktree for a shell session (no CLI options)
    // on a stored git project and spawn the shell inside it. Managed Claude/Codex sessions
    // instead carry worktree intent inside their own options block.
    val worktree: Boolean = false,
    val branch_name: String? = null,
    // "chat" | "terminal"; null keeps the request byte-identical to older clients.
    val ui: String? = null,
    // Chat only: continue the agent's stored session with this id instead of a new one.
    val chat_resume_id: String? = null,
    // The ref the new worktree starts from; null = the agent's default branch.
    val base_branch: String? = null,
)

@Serializable
data class ScrollbackResponse(
    val session_id: String,
    val lines: List<String>,
    val total: Int,
)
