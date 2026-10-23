package com.mobaide.app.data.local

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Migration guard for [ClaudeLaunchPrefs]: a blob persisted by a build that
 * predates `skipPermissions` / `noMemory` / `planMode` must decode without
 * error and materialize the new toggles as `false` — dropping any of the
 * property defaults would crash every upgrading install on its cached prefs.
 */
class ClaudeLaunchPrefsTest {

    // Mirrors PreferencesStore.cacheJson (lenient decode for cached blobs).
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    @Test
    fun legacyBlob_withoutToggleFields_materializesThemFalse() {
        val legacy = """
            {"model":"opus","effort":"max","fastMode":true,"ultracode":false,
             "appendSystemPrompt":"be terse"}
        """.trimIndent()

        val decoded = json.decodeFromString(ClaudeLaunchPrefs.serializer(), legacy)

        assertEquals("opus", decoded.model)
        assertEquals("max", decoded.effort)
        assertTrue(decoded.fastMode)
        assertEquals("be terse", decoded.appendSystemPrompt)
        assertFalse(decoded.skipPermissions)
        assertFalse(decoded.noMemory)
        assertFalse(decoded.planMode)
    }

    @Test
    fun encodedBlob_alwaysCarriesToggleFields_forCrossVersionReads() {
        // encodeDefaults=true in the store means even all-false prefs write the
        // toggle keys explicitly, so a later build never misreads absence.
        val encoded = json.encodeToString(
            ClaudeLaunchPrefs.serializer(),
            ClaudeLaunchPrefs(skipPermissions = true, planMode = true),
        )

        assertTrue(encoded.contains("\"skipPermissions\":true"))
        assertTrue(encoded.contains("\"noMemory\":false"))
        assertTrue(encoded.contains("\"planMode\":true"))
    }

    @Test
    fun legacyBlob_withoutFocusMode_restoresFocusViewOn() {
        val legacy = """{"model":"opus","effort":"max","debug":false}"""

        val decoded = json.decodeFromString(ClaudeLaunchPrefs.serializer(), legacy)

        assertTrue(decoded.focusMode)
    }

    @Test
    fun savedFocusModeOff_survivesTheRoundTrip() {
        val encoded = json.encodeToString(ClaudeLaunchPrefs.serializer(), ClaudeLaunchPrefs(focusMode = false))

        assertTrue(encoded.contains("\"focusMode\":false"))
        assertFalse(json.decodeFromString(ClaudeLaunchPrefs.serializer(), encoded).focusMode)
    }
}
