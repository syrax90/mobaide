package com.mobaide.app.data.local

import kotlinx.serialization.json.Json
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Migration guard for the Code Mode toggles: a launch-prefs blob saved before
 * `codeMode` existed must restore each agent's own default — on for OpenCode
 * (OpenCode 2 runs Code Mode by default), off for Claude Code and Pi.
 */
class CodeModeLaunchPrefsTest {

    // Mirrors PreferencesStore.cacheJson (lenient decode for cached blobs).
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    @Test
    fun legacyBlobs_withoutCodeMode_restoreEachAgentsDefault() {
        val claude = json.decodeFromString(ClaudeLaunchPrefs.serializer(), """{"model":"opus","terminalUi":true}""")
        val opencode = json.decodeFromString(OpenCodeLaunchPrefs.serializer(), """{"autoApprove":true,"terminalUi":false}""")
        val pi = json.decodeFromString(PiLaunchPrefs.serializer(), """{"terminalUi":true}""")

        assertFalse(claude.codeMode)
        assertTrue("an upgrade must not turn OpenCode's Code Mode off", opencode.codeMode)
        assertFalse(pi.codeMode)
    }

    @Test
    fun openCodeCodeModeOff_survivesTheRoundTrip() {
        val encoded = json.encodeToString(OpenCodeLaunchPrefs.serializer(), OpenCodeLaunchPrefs(codeMode = false))

        assertFalse(json.decodeFromString(OpenCodeLaunchPrefs.serializer(), encoded).codeMode)
    }
}
