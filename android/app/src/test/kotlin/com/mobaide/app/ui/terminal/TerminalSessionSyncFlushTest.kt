package com.mobaide.app.ui.terminal

import android.os.Looper
import com.termux.terminal.TerminalSession
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.Duration

/**
 * Robolectric tests for the synchronized-output render deferral in the forked
 * [TerminalSession.emitOutput] (needs a real main [Looper] for the flush Handler).
 *
 * The contract: while DECSET 2026 is active, `emitOutput` keeps parsing but suppresses
 * the `onTextChanged` fan-out; the first non-sync append (the ESU chunk) notifies
 * immediately and cancels the safety timer; a leading-edge 150 ms timer forces a paint
 * during a long sync window WITHOUT clearing the mode bit (per spec, timeout renders —
 * it does not reset), and re-arms so a stream inside one long window keeps painting.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalSessionSyncFlushTest {

    private lateinit var session: TerminalSession
    private var notifies = 0

    @Before
    fun setUp() {
        session = TerminalSession(TerminalSessionCallback(onScreenUpdated = { notifies++ }))
        session.updateSize(80, 24, 12, 24)
    }

    /** Emit [sequence] through the production path and run the posted main-looper task. */
    private fun emit(sequence: String) {
        val bytes = sequence.toByteArray(Charsets.UTF_8)
        session.emitOutput(bytes, bytes.size)
        shadowOf(Looper.getMainLooper()).idle()
    }

    private fun advanceTime(ms: Long) {
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(ms))
    }

    @Test
    fun nonSyncOutput_notifiesPerChunk() {
        emit("hello")
        emit("world")

        assertEquals(2, notifies)
    }

    @Test
    fun bsuAndEsuInOneChunk_notifiesImmediately() {
        emit(SYNC_ON + "atomic frame" + SYNC_OFF)

        assertEquals("a balanced chunk must behave exactly like plain output", 1, notifies)
    }

    @Test
    fun syncActive_defersNotify_untilEsuChunk() {
        emit(SYNC_ON + "partial frame line 1")
        emit("partial frame line 2")
        assertEquals("mid-frame chunks must not paint (this is the tearing fix)", 0, notifies)

        emit("frame end" + SYNC_OFF)

        assertEquals("the ESU chunk paints the whole frame at once", 1, notifies)
    }

    @Test
    fun esuChunk_cancelsSafetyTimer_noDoubleFlush() {
        emit(SYNC_ON + "frame")
        emit(SYNC_OFF)
        assertEquals(1, notifies)

        advanceTime(SYNC_TIMEOUT_MS * 2)

        assertEquals("the cancelled timer must not fire a second paint", 1, notifies)
    }

    @Test
    fun syncTimeout_forcesPaint_andKeepsModeSet() {
        emit(SYNC_ON + "stuck frame")
        assertEquals(0, notifies)

        advanceTime(SYNC_TIMEOUT_MS)

        assertEquals("the safety valve must paint a wedged sync window", 1, notifies)
        assertTrue(
            "timeout renders but must NOT clear the mode — only ESU/RIS do",
            requireNotNull(session.emulator).isSynchronizedOutputActive,
        )
    }

    @Test
    fun leadingEdgeTimer_rearmsAcrossALongSyncWindow() {
        emit(SYNC_ON + "stream part 1")
        advanceTime(SYNC_TIMEOUT_MS)
        assertEquals(1, notifies)

        emit("stream part 2") // still inside the sync window
        assertEquals("suppression resumes after a timeout flush", 1, notifies)

        advanceTime(SYNC_TIMEOUT_MS)

        assertEquals(
            "a program streaming inside one long sync window keeps painting every timeout",
            2,
            notifies,
        )
    }

    private companion object {
        const val SYNC_ON = "[?2026h"
        const val SYNC_OFF = "[?2026l"

        /** Mirrors TerminalSession.SYNC_UPDATE_TIMEOUT_MS (private in the fork). */
        const val SYNC_TIMEOUT_MS = 150L
    }
}
