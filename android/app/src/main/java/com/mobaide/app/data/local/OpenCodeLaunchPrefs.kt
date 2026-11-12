package com.mobaide.app.data.local

import kotlinx.serialization.Serializable

/**
 * Persisted last-used OpenCode launch selections, re-applied to the
 * create-session wizard on the next launch (mirrors [AntigravityLaunchPrefs]).
 * Worktree/branch are intentionally excluded — they are per-session, not
 * reusable defaults.
 */
@Serializable
data class OpenCodeLaunchPrefs(
    val autoApprove: Boolean = false,
    val terminalUi: Boolean = false,
)
