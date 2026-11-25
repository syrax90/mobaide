package com.mobaide.app.data.local

import kotlinx.serialization.Serializable

/**
 * Persisted last-used Codex launch selections, re-applied to the create-session
 * wizard on the next launch (mirrors [ClaudeLaunchPrefs]). Stored as a single
 * JSON blob; lenient decoding tolerates added fields so an older cached blob
 * never crashes a newer build. Worktree/branch are intentionally excluded —
 * they are per-session, not reusable defaults.
 */
@Serializable
data class CodexLaunchPrefs(
    val model: String? = null,
    val sandbox: String = "workspace-write",
    val approval: String = "on-request",
    val fullAuto: Boolean = false,
    // Codex Auto review. Older blobs decode to off.
    val autoReview: Boolean = false,
    val reasoningEffort: String = "high",
    // The classic terminal instead of the chat view. Older blobs decode to off.
    val terminalUi: Boolean = false,
)

/**
 * [approval] with the `untrusted` policy, which Codex 0.149 retired and now
 * rejects at launch, read as `on-request`, the nearest policy that still asks.
 */
fun currentCodexApproval(approval: String): String =
    if (approval == "untrusted") "on-request" else approval
