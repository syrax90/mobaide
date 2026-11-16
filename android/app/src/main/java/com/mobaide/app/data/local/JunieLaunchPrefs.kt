package com.mobaide.app.data.local

import kotlinx.serialization.Serializable

/**
 * Persisted last-used Junie launch selections, re-applied to the
 * create-session wizard on the next launch (mirrors [CursorLaunchPrefs]).
 * Worktree/branch are intentionally excluded — they are per-session, not
 * reusable defaults.
 */
@Serializable
data class JunieLaunchPrefs(
    val planMode: Boolean = false,
    val brave: Boolean = false,
    val terminalUi: Boolean = false,
)
