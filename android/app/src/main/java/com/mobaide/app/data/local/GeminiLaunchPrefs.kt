package com.mobaide.app.data.local

import kotlinx.serialization.Serializable

/**
 * Persisted last-used Gemini launch selections, re-applied to the create-session
 * wizard on the next launch (mirrors [CodexLaunchPrefs]). Stored as a single
 * JSON blob; lenient decoding tolerates added fields so an older cached blob
 * never crashes a newer build. Worktree/branch are intentionally excluded —
 * they are per-session, not reusable defaults.
 */
@Serializable
data class GeminiLaunchPrefs(
    val model: String? = null,
    val approvalMode: String = "auto_edit",
)
