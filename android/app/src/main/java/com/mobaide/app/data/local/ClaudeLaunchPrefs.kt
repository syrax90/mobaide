package com.mobaide.app.data.local

import kotlinx.serialization.Serializable

/**
 * Persisted last-used Claude launch selections, re-applied to the create-session
 * wizard on the next launch (mirrors the last-used-project pre-fill in
 * [PreferencesStore]). Backs two independent per-kind buckets — Claude Code and
 * Auto Scan — so each mode remembers its own choices; [planMode] is meaningful
 * only in the Auto Scan bucket. Stored as a single JSON blob; lenient decoding
 * tolerates added fields so an older cached blob never crashes a newer build.
 */
@Serializable
data class ClaudeLaunchPrefs(
    val model: String? = null,
    val effort: String = "high",
    val fastMode: Boolean = false,
    val ultracode: Boolean = false,
    val appendSystemPrompt: String = "",
    val skipPermissions: Boolean = false,
    val noMemory: Boolean = false,
    val planMode: Boolean = false,
    val debug: Boolean = false,
)
