package com.mobaide.app.data.local

import kotlinx.serialization.Serializable

/**
 * Persisted last-used Antigravity launch selections, re-applied to the
 * create-session wizard on the next launch (mirrors [CodexLaunchPrefs]). Stored
 * as a single JSON blob; lenient decoding tolerates added fields so an older
 * cached blob never crashes a newer build. Worktree/branch and additional
 * directories are intentionally excluded — they are per-session, not reusable
 * defaults.
 */
@Serializable
data class AntigravityLaunchPrefs(
    val model: String? = null,
    val skipPermissions: Boolean = false,
    val sandbox: Boolean = false,
)
