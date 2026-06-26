package com.mobaide.app.data.local

import kotlinx.serialization.Serializable

/**
 * Persisted last-used Claude launch selections, re-applied to the create-session
 * wizard on the next launch (mirrors the last-used-project pre-fill in
 * [PreferencesStore]). Stored as a single JSON blob; lenient decoding tolerates
 * added fields so an older cached blob never crashes a newer build.
 */
@Serializable
data class ClaudeLaunchPrefs(
    val model: String? = null,
    val effort: String = "high",
    val fastMode: Boolean = false,
    val ultracode: Boolean = false,
    val appendSystemPrompt: String = "",
)
