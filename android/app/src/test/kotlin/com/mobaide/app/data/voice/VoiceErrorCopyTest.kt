package com.mobaide.app.data.voice

import org.junit.Assert.assertEquals
import org.junit.Test

/** A failed cleanup names the CLI of the session's kind. */
class VoiceErrorCopyTest {

    private fun cleanup(code: String, kind: String?) = VoiceErrorCopy.cleanup(code, "MacBook-Pro", kind)

    @Test
    fun cleanup_namesClaudeCode() {
        assertEquals("Claude Code isn't installed on MacBook-Pro.", cleanup(VoiceCodes.CLEANUP_UNAVAILABLE, "claude"))
        assertEquals(
            "Claude Code on MacBook-Pro is too old. Run \"claude update\" there.",
            cleanup(VoiceCodes.CLEANUP_OUTDATED, "claude"),
        )
        assertEquals("Claude Code took too long.", cleanup(VoiceCodes.CLEANUP_TIMEOUT, "claude"))
        assertEquals("Claude Code reported an error.", cleanup(VoiceCodes.CLEANUP_FAILED, "claude"))
    }

    @Test
    fun cleanup_namesCodex() {
        assertEquals("Codex isn't installed on MacBook-Pro.", cleanup(VoiceCodes.CLEANUP_UNAVAILABLE, "codex"))
        assertEquals(
            "Codex on MacBook-Pro is too old. Run \"codex update\" there.",
            cleanup(VoiceCodes.CLEANUP_OUTDATED, "codex"),
        )
        assertEquals("Codex took too long.", cleanup(VoiceCodes.CLEANUP_TIMEOUT, "codex"))
        assertEquals("Codex reported an error.", cleanup(VoiceCodes.CLEANUP_FAILED, "codex"))
    }

    @Test
    fun cleanup_namesAntigravity_whoseCommandIsAgy() {
        assertEquals(
            "Antigravity isn't installed on MacBook-Pro.",
            cleanup(VoiceCodes.CLEANUP_UNAVAILABLE, "antigravity"),
        )
        assertEquals(
            "Antigravity on MacBook-Pro is too old. Run \"agy update\" there.",
            cleanup(VoiceCodes.CLEANUP_OUTDATED, "antigravity"),
        )
        assertEquals("Antigravity took too long.", cleanup(VoiceCodes.CLEANUP_TIMEOUT, "antigravity"))
        assertEquals("Antigravity reported an error.", cleanup(VoiceCodes.GENERIC, "antigravity"))
    }

    @Test
    fun cleanup_ofAnUnknownKind_namesTheSessionsAgent_withoutACommand() {
        for (kind in listOf("shell", null)) {
            assertEquals(
                "The session's AI agent on MacBook-Pro is too old. Update it there.",
                cleanup(VoiceCodes.CLEANUP_OUTDATED, kind),
            )
            assertEquals("The session's AI agent reported an error.", cleanup(VoiceCodes.CLEANUP_FAILED, kind))
        }
    }

    @Test
    fun cleanup_busyAndNetwork_nameNoCli() {
        assertEquals("Another cleanup was still running.", cleanup(VoiceCodes.CLEANUP_BUSY, "codex"))
        assertEquals("The connection dropped.", cleanup(VoiceCodes.NETWORK, "antigravity"))
    }
}
