package com.mobaide.app.ui.terminal

import com.termux.terminal.TerminalSession
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Unit tests for the vendored emulator's DECSET 2026 (synchronized output) support.
 *
 * The contract: `CSI ?2026 h/l` toggles [com.termux.terminal.TerminalEmulator
 * .isSynchronizedOutputActive], the generic DECRQM handler (`CSI ?2026 $p`) reports the
 * mode as settable (`?2026;1$y` set / `?2026;2$y` reset — never `0` = unrecognized,
 * which is what programs like Claude Code probe before enabling synchronized redraws),
 * and a full terminal reset (RIS) clears the bit so a crashed TUI cannot wedge
 * rendering off. The render-deferral half of the feature lives in
 * `TerminalSession.emitOutput` and is covered by [TerminalSessionSyncFlushTest].
 *
 * Escape bytes are fed straight to the JVM-hosted emulator via `emulator.append`;
 * DECRQM replies are captured through the session's input listener (the same path
 * that would carry them to the remote PTY in production).
 */
class TerminalSynchronizedOutputTest {

    private lateinit var session: TerminalSession
    private val replies = StringBuilder()

    @Before
    fun setUp() {
        session = TerminalSession(TerminalSessionCallback())
        session.updateSize(80, 24, 12, 24)
        session.setOnInputListener { data, offset, count ->
            replies.append(String(data, offset, count, Charsets.UTF_8))
        }
    }

    private fun feed(sequence: String) {
        val bytes = sequence.toByteArray(Charsets.UTF_8)
        requireNotNull(session.emulator).append(bytes, bytes.size)
    }

    @Test
    fun decset2026_setAndReset_toggleTheMode() {
        feed(SYNC_ON)
        assertTrue(requireNotNull(session.emulator).isSynchronizedOutputActive)

        feed(SYNC_OFF)
        assertFalse(requireNotNull(session.emulator).isSynchronizedOutputActive)
    }

    @Test
    fun decrqm_reportsReset_whenModeOff() {
        feed(DECRQM_2026)

        assertEquals(
            "DECRQM must advertise 2026 as supported-but-reset — a 0 reply would tell " +
                "Claude Code's auto-detection the terminal cannot synchronize output",
            "[?2026;2\$y",
            replies.toString(),
        )
    }

    @Test
    fun decrqm_reportsSet_whenModeOn() {
        feed(SYNC_ON + DECRQM_2026)

        assertEquals("[?2026;1\$y", replies.toString())
    }

    @Test
    fun terminalReset_clearsTheMode() {
        feed(SYNC_ON)
        assertTrue(requireNotNull(session.emulator).isSynchronizedOutputActive)

        requireNotNull(session.emulator).reset()

        assertFalse(
            "RIS must clear a stuck sync mode (crashed TUI mid-BSU)",
            requireNotNull(session.emulator).isSynchronizedOutputActive,
        )
    }

    @Test
    fun saveRestore_roundTripsTheMode() {
        feed(SYNC_ON + "[?2026s" + SYNC_OFF)
        assertFalse(requireNotNull(session.emulator).isSynchronizedOutputActive)

        feed("[?2026r")

        assertTrue(
            "CSI ?2026 s/r must round-trip through the generic DECSET save/restore path",
            requireNotNull(session.emulator).isSynchronizedOutputActive,
        )
    }

    private companion object {
        const val SYNC_ON = "[?2026h"
        const val SYNC_OFF = "[?2026l"
        const val DECRQM_2026 = "[?2026\$p"
    }
}
