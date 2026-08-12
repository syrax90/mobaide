package com.mobaide.app.ui.terminal

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The unwatched-session suspend policy. Must stay byte-for-byte in step with
 * the web client's `terminal/terminalSuspend.ts` and iOS's `TerminalSuspend`.
 */
class TerminalSuspendPolicyTest {

    @Test
    fun `keeps a quiet session attached however long it goes unread`() {
        // The whole point of the byte term: an idle session costs nothing to
        // hold, and suspending it would charge a full ring replay to save that
        // nothing.
        assertFalse(
            TerminalSuspend.shouldSuspend(
                unwatchedMs = TerminalSuspend.GRACE_MS * 100,
                bytes = TerminalSuspend.RECOUP_BYTES - 1,
            ),
        )
    }

    @Test
    fun `keeps a busy session attached inside the grace window`() {
        // The time term: leaving the app for a moment is not worth a replay
        // even on a chatty session.
        assertFalse(
            TerminalSuspend.shouldSuspend(
                unwatchedMs = TerminalSuspend.GRACE_MS - 1,
                bytes = TerminalSuspend.RECOUP_BYTES * 4,
            ),
        )
    }

    @Test
    fun `suspends once the session has both gone unread and spent a ring`() {
        assertTrue(
            TerminalSuspend.shouldSuspend(
                unwatchedMs = TerminalSuspend.GRACE_MS,
                bytes = TerminalSuspend.RECOUP_BYTES,
            ),
        )
    }

    @Test
    fun `cuts a firehose off without waiting out the grace`() {
        assertTrue(
            TerminalSuspend.shouldSuspend(
                unwatchedMs = 0,
                bytes = TerminalSuspend.FIREHOSE_BYTES,
            ),
        )
    }

    @Test
    fun `holds its boundaries inclusive on every term`() {
        // Pinned so a refactor cannot silently turn a `>=` into a `>` and shift
        // the policy by one byte or one millisecond.
        assertFalse(
            TerminalSuspend.shouldSuspend(
                unwatchedMs = TerminalSuspend.GRACE_MS,
                bytes = TerminalSuspend.RECOUP_BYTES - 1,
            ),
        )
        assertTrue(
            TerminalSuspend.shouldSuspend(
                unwatchedMs = TerminalSuspend.GRACE_MS - 1,
                bytes = TerminalSuspend.FIREHOSE_BYTES,
            ),
        )
    }

    @Test
    fun `prices the recoup threshold at one ring and the firehose above it`() {
        // The constants are the policy, and they are shared across three
        // clients. `RECOUP_BYTES` below the agent's 4 MiB ring would make every
        // suspend a guaranteed net loss.
        assertEquals(4L * 1024 * 1024, TerminalSuspend.RECOUP_BYTES)
        assertEquals(60_000L, TerminalSuspend.GRACE_MS)
        assertTrue(TerminalSuspend.FIREHOSE_BYTES > TerminalSuspend.RECOUP_BYTES)
    }
}
