package com.mobaide.app.ui.terminal

import android.content.Context
import android.os.Looper
import android.os.SystemClock
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import com.termux.terminal.TerminalSession
import com.termux.view.TerminalView
import com.termux.view.TerminalViewClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.Duration

/**
 * Robolectric tests for the gates guarding input-row resolution in the forked
 * `TerminalView.onSingleTapUp` and `dispatchInputRowGesture`.
 *
 * The tap-to-raise contract: when [TerminalViewClient.onTerminalTapToRaiseKeyboard]
 * returns true (composer hidden — the terminal IS the input field), a confirmed single
 * tap is fully consumed as an IME-raise request BEFORE input-row resolution, so tapping
 * a prompt-prefixed row can never fire [TerminalViewClient.onTerminalRowTapped]'s
 * CR/execute submit.
 *
 * The pointer-ownership contract: row-tap submit is a plain-shell affordance, so it is
 * skipped whenever the program owns the pointer — while it tracks the mouse (the tap
 * already reached it as a click from `onUp`) or holds the alternate screen. Every
 * negative assertion below has `gateInactive_promptRowTap_stillSubmits` as its control:
 * the setups differ only by the DECSET fed to the emulator.
 *
 * The synchronized-output contract: while a DECSET 2026 frame is parsed into the screen
 * but deliberately not painted yet, the drawn rows are older than the ones the lookup
 * reads, so both the single-tap submit and the double-tap copy-to-input refuse rather
 * than resolve a line the user never saw — and both come back at the safety flush,
 * which is the only thing that bounds how far the glass can lag. That reading is taken
 * twice, at touch-up and again at dispatch, because the 150 ms flush lands inside the
 * ~300 ms the recognizer holds the tap: only the latched one still describes the glass
 * the finger actually landed on.
 *
 * Real [MotionEvent]s are driven through [TerminalView.onTouchEvent]; the gesture
 * recognizer maps single taps to `onSingleTapConfirmed`, which arrives via a delayed
 * main-looper message after the double-tap timeout — hence the explicit `idleFor`.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class TerminalViewTapToRaiseTest {

    private lateinit var context: Context
    private lateinit var view: TerminalView
    private lateinit var session: TerminalSession
    private lateinit var client: RecordingClient

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        client = RecordingClient()
        session = TerminalSession(TerminalSessionCallback())
        view = TerminalView(context, null).apply {
            setTerminalViewClient(client)
            setTextSize(24)
            attachSession(session)
            measure(
                View.MeasureSpec.makeMeasureSpec(480, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(800, View.MeasureSpec.EXACTLY),
            )
            layout(0, 0, 480, 800)
        }
        assertNotNull(
            "precondition: layout must have initialized the emulator (renderer metrics > 0)",
            session.emulator,
        )
    }

    private fun feed(text: String) {
        val bytes = text.toByteArray(Charsets.UTF_8)
        requireNotNull(session.emulator).append(bytes, bytes.size)
    }

    /** Down+up at the top-left text row. The confirmed single tap is still queued
     *  at this point, due [CONFIRMED_TAP_DELAY_MS] after the finger went down. */
    private fun pressAndLiftTopRow() {
        val downTime = SystemClock.uptimeMillis()
        val down = MotionEvent.obtain(downTime, downTime, MotionEvent.ACTION_DOWN, 10f, 1f, 0)
        val up = MotionEvent.obtain(downTime, downTime + 50, MotionEvent.ACTION_UP, 10f, 1f, 0)
        view.onTouchEvent(down)
        view.onTouchEvent(up)
        down.recycle()
        up.recycle()
    }

    private fun advanceTime(ms: Long) {
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(ms))
    }

    /** Down+up at the top-left text row, then idle past the double-tap timeout so the
     *  gesture recognizer delivers the confirmed single tap. */
    private fun singleTapTopRow() {
        pressAndLiftTopRow()
        advanceTime(1_000)
    }

    /** Two taps on the top text row, spaced inside the double-tap window so the
     *  recognizer resolves them as `onDoubleTap` instead of a confirmed single tap. */
    private fun doubleTapTopRow() {
        val downTime = SystemClock.uptimeMillis()
        val events = listOf(
            MotionEvent.obtain(downTime, downTime, MotionEvent.ACTION_DOWN, 10f, 1f, 0),
            MotionEvent.obtain(downTime, downTime + 50, MotionEvent.ACTION_UP, 10f, 1f, 0),
            MotionEvent.obtain(downTime + 100, downTime + 100, MotionEvent.ACTION_DOWN, 10f, 1f, 0),
            MotionEvent.obtain(downTime + 100, downTime + 150, MotionEvent.ACTION_UP, 10f, 1f, 0),
        )
        events.forEach {
            view.onTouchEvent(it)
            it.recycle()
        }
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(1_000))
    }

    /**
     * Two taps on the top text row spaced far enough apart in main-looper time for a
     * pending safety flush to fire between the first lift and the confirmed double tap,
     * yet still inside the double-tap window. Returns whether a paint was still deferred
     * when the second tap started — i.e. what the dispatch-time half of the gate sees.
     */
    private fun doubleTapAcrossTheFlush(): Boolean {
        val firstDown = SystemClock.uptimeMillis()
        listOf(
            MotionEvent.obtain(firstDown, firstDown, MotionEvent.ACTION_DOWN, 10f, 1f, 0),
            MotionEvent.obtain(firstDown, firstDown + 20, MotionEvent.ACTION_UP, 10f, 1f, 0),
        ).forEach {
            view.onTouchEvent(it)
            it.recycle()
        }
        advanceTime(SYNC_TIMEOUT_MS + 20)
        val deferredAtSecondTap = session.isScreenUpdateDeferred
        val secondDown = SystemClock.uptimeMillis()
        listOf(
            MotionEvent.obtain(secondDown, secondDown, MotionEvent.ACTION_DOWN, 10f, 1f, 0),
            MotionEvent.obtain(secondDown, secondDown + 20, MotionEvent.ACTION_UP, 10f, 1f, 0),
        ).forEach {
            view.onTouchEvent(it)
            it.recycle()
        }
        advanceTime(1_000)
        return deferredAtSecondTap
    }

    private fun recordSentBytes(): StringBuilder {
        val sentBytes = StringBuilder()
        session.setOnInputListener { data, offset, count ->
            sentBytes.append(String(data, offset, count, Charsets.ISO_8859_1))
        }
        return sentBytes
    }

    /**
     * Parses [text] through the production PTY path, which is what defers the paint.
     * [feed] appends straight to the emulator and never defers anything, so every
     * other test in this file leaves this gate open.
     */
    private fun emitOutput(text: String) {
        val bytes = text.toByteArray(Charsets.UTF_8)
        session.emitOutput(bytes, bytes.size, false)
        shadowOf(Looper.getMainLooper()).idle()
    }

    @Test
    fun gateActive_tapRaisesKeyboard_andNeverSubmitsPromptRow() {
        feed("> echo hi") // the TUI's own input row, exactly what the user taps by mistake
        client.tapToRaise = true

        singleTapTopRow()

        assertEquals("the tap must be consumed as an IME raise", 1, client.raiseCount)
        assertTrue(
            "row-tap submit is the footgun the gate exists to prevent",
            client.tappedRows.isEmpty(),
        )
    }

    @Test
    fun gateInactive_promptRowTap_stillSubmits() {
        feed("> echo hi")
        client.tapToRaise = false

        singleTapTopRow()

        assertEquals(0, client.raiseCount)
        assertEquals(
            "without the gate the pre-existing row-tap submit contract must hold",
            listOf("echo hi"),
            client.tappedRows,
        )
    }

    @Test
    fun gateActive_mouseTrackingTui_tapRaisesInsteadOfClickingRemoteUi() {
        val sentBytes = recordSentBytes()
        feed(MOUSE_TRACKING_ON) // e.g. a full-screen TUI with mouse support
        client.tapToRaise = true

        singleTapTopRow()

        assertEquals("the tap must raise the IME", 1, client.raiseCount)
        assertEquals(
            "the ACTION_UP mouse fast path fires before the confirmed single tap — with " +
                "the gate active it must not click the remote UI",
            "",
            sentBytes.toString(),
        )
    }

    @Test
    fun gateInactive_mouseTrackingTui_tapClicksProgramWithoutSubmitting() {
        val sentBytes = recordSentBytes()
        feed(MOUSE_TRACKING_ON)
        feed("> echo hi") // Claude Code's own prompt row, drawn on the normal buffer
        client.tapToRaise = false

        singleTapTopRow()

        assertEquals(0, client.raiseCount)
        assertTrue(
            "the mouse-tracking click contract must hold — the program positions its own cursor",
            sentBytes.isNotEmpty(),
        )
        assertTrue(
            "the click already delivered the tap; resolving the row would submit a CR on top of it",
            client.tappedRows.isEmpty(),
        )
    }

    @Test
    fun gateInactive_alternateScreen_promptRowTapDoesNothing() {
        val sentBytes = recordSentBytes()
        feed(ALTERNATE_SCREEN_ON) // `less`, `man`, plain vim: no mouse reporting at all
        feed("> echo hi")
        client.tapToRaise = false

        singleTapTopRow()

        assertTrue(
            "alternate-screen rows are program-drawn chrome — submitting one Ctrl+Cs the program",
            client.tappedRows.isEmpty(),
        )
        assertEquals("a program that does not track the mouse gets no click either", "", sentBytes.toString())
    }

    /**
     * Doubles as the control for [gateInactive_mouseTrackingTui_tapClicksProgramWithoutSubmitting]:
     * the ungated double tap runs the same row resolution the single tap skips, so the row
     * being resolvable here is what makes that test's empty `tappedRows` the gate's doing.
     */
    @Test
    fun gateInactive_mouseTrackingTui_doubleTapStillCopiesToInput() {
        feed(MOUSE_TRACKING_ON)
        feed("> echo hi")
        client.tapToRaise = false

        doubleTapTopRow()

        assertEquals(
            "copy-to-composer is non-destructive and is the replacement affordance for re-running a message",
            listOf("echo hi"),
            client.copiedRows,
        )
        assertTrue("a double tap must never resolve as a submit", client.tappedRows.isEmpty())
    }

    /** Control for [gateInactive_alternateScreen_promptRowTapDoesNothing], as above. */
    @Test
    fun gateInactive_alternateScreen_doubleTapStillCopiesToInput() {
        feed(ALTERNATE_SCREEN_ON)
        feed("> echo hi")
        client.tapToRaise = false

        doubleTapTopRow()

        assertEquals(listOf("echo hi"), client.copiedRows)
        assertTrue(client.tappedRows.isEmpty())
    }

    @Test
    fun deferredPaint_promptRowTap_doesNotSubmit() {
        feed("> echo hi")
        client.tapToRaise = false
        // The confirmed tap arrives CONFIRMED_TAP_DELAY_MS after the finger goes down
        // and the safety flush SYNC_TIMEOUT_MS after the deferral is armed, so the
        // deferral has to be armed after the lift for the tap to resolve inside it.
        pressAndLiftTopRow()
        advanceTime(CONFIRMED_TAP_DELAY_MS - SYNC_TIMEOUT_MS + 50)
        emitOutput(SYNC_OUTPUT_ON)
        var deferredWhenTapLanded: Boolean? = null
        client.onConfirmedTap = { deferredWhenTapLanded = session.isScreenUpdateDeferred }

        advanceTime(1_000)

        assertEquals(
            "the interleaving is the whole test: the confirmed tap has to land before the flush",
            true,
            deferredWhenTapLanded,
        )
        assertTrue(
            "the drawn rows are older than the screen findInputMessage reads, and a program can " +
                "duty-cycle inside the window — paint benign, restore hostile before each flush",
            client.tappedRows.isEmpty(),
        )
    }

    /**
     * The interleaving Codex found: the finger lifts while the paint is deferred, the
     * 150 ms flush lands inside the ~300 ms the recognizer holds the tap, and the
     * confirmed tap arrives with the gate already reopened. The dispatch-time read on
     * its own waves that through, so what has to refuse is the reading taken at lift.
     *
     * Control: [afterTheSafetyFlush_promptRowTap_submitsAgain], where the whole touch
     * happens after the flush.
     */
    @Test
    fun theFlushLandingInsideTheConfirmGap_promptRowTap_doesNotSubmit() {
        feed("> echo hi")
        client.tapToRaise = false
        emitOutput(SYNC_OUTPUT_ON)
        assertTrue("precondition: the finger must lift inside the window", session.isScreenUpdateDeferred)
        var deferredWhenTapLanded: Boolean? = null
        client.onConfirmedTap = { deferredWhenTapLanded = session.isScreenUpdateDeferred }

        pressAndLiftTopRow()
        advanceTime(1_000)

        assertEquals(
            "the interleaving is the whole test: the flush has to land before the confirmed tap, " +
                "leaving the dispatch-time half of the gate nothing to refuse on",
            false,
            deferredWhenTapLanded,
        )
        assertTrue(
            "what the user decided against is the glass as it looked at lift — a row swapped in " +
                "behind the deferred paint is not a row anyone tapped",
            client.tappedRows.isEmpty(),
        )
    }

    /**
     * Control for [deferredPaint_promptRowTap_doesNotSubmit] and for
     * [theFlushLandingInsideTheConfirmGap_promptRowTap_doesNotSubmit]: the flush lands
     * before the finger goes down, which is the whole of what "the gate reopened" can
     * mean once the reading is latched at lift.
     */
    @Test
    fun afterTheSafetyFlush_promptRowTap_submitsAgain() {
        feed("> echo hi")
        client.tapToRaise = false
        emitOutput(SYNC_OUTPUT_ON)
        assertTrue("precondition: the paint starts out deferred", session.isScreenUpdateDeferred)
        advanceTime(SYNC_TIMEOUT_MS)
        assertFalse(session.isScreenUpdateDeferred)

        singleTapTopRow()

        assertEquals(
            "a touch that starts after the flush sees the painted rows, so it must submit as before",
            listOf("echo hi"),
            client.tappedRows,
        )
        assertTrue(
            "and it reopened while the program still holds the mode — the mode bit has no timeout, " +
                "so gating on that instead would kill row taps for as long as a program likes",
            requireNotNull(session.emulator).isSynchronizedOutputActive,
        )
    }

    @Test
    fun deferredPaint_doubleTap_doesNotCopyToInput() {
        feed("> echo hi")
        client.tapToRaise = false
        emitOutput(SYNC_OUTPUT_ON)
        assertTrue("precondition: the paint must still be pending", session.isScreenUpdateDeferred)

        doubleTapTopRow()

        assertTrue(
            "onDoubleTap resolves on the second touch-down, still inside the window; spoofed text " +
                "in the composer is the first half of the same attack",
            client.copiedRows.isEmpty(),
        )
    }

    /**
     * [theFlushLandingInsideTheConfirmGap_promptRowTap_doesNotSubmit] for the other
     * gesture that goes through `dispatchInputRowGesture`. The double tap resolves on
     * the second touch-down, so a flush landing between the two taps clears the gate
     * just as the confirm gap does for the single tap — and the latch from the first
     * lift is again the only thing left holding.
     *
     * Control: [afterTheSafetyFlush_doubleTap_copiesToInputAgain].
     */
    @Test
    fun theFlushLandingBetweenTheTaps_doubleTap_doesNotCopyToInput() {
        feed("> echo hi")
        client.tapToRaise = false
        emitOutput(SYNC_OUTPUT_ON)
        assertTrue("precondition: the first finger must lift inside the window", session.isScreenUpdateDeferred)

        val deferredAtSecondTap = doubleTapAcrossTheFlush()

        assertFalse(
            "the interleaving is the whole test: the flush has to land between the two taps",
            deferredAtSecondTap,
        )
        assertTrue(
            "spoofed text in the composer is the first half of the same attack, so it fails closed " +
                "on the same latched reading",
            client.copiedRows.isEmpty(),
        )
    }

    /** Control for [deferredPaint_doubleTap_doesNotCopyToInput]: same setup, flush first. */
    @Test
    fun afterTheSafetyFlush_doubleTap_copiesToInputAgain() {
        feed("> echo hi")
        client.tapToRaise = false
        emitOutput(SYNC_OUTPUT_ON)
        advanceTime(SYNC_TIMEOUT_MS)
        assertFalse(session.isScreenUpdateDeferred)

        doubleTapTopRow()

        assertEquals(listOf("echo hi"), client.copiedRows)
    }

    private companion object {
        /** DECSET 1049 — switch to the alternate screen buffer, clearing it. */
        const val ALTERNATE_SCREEN_ON = "[?1049h"

        /** DECSET 1000 — X11 press/release mouse reporting. Does not switch buffers. */
        const val MOUSE_TRACKING_ON = "[?1000h"

        /** DECSET 2026 — synchronized output: keep parsing, hold the paint. */
        const val SYNC_OUTPUT_ON = "[?2026h"

        /** Mirrors TerminalSession.SYNC_UPDATE_TIMEOUT_MS (private in the fork). */
        const val SYNC_TIMEOUT_MS = 150L

        /** ViewConfiguration.getDoubleTapTimeout(), i.e. how long after touch-down the
         *  recognizer delivers `onSingleTapConfirmed`. */
        const val CONFIRMED_TAP_DELAY_MS = 300L
    }

    /** Full [TerminalViewClient] with recording hooks for the tap paths under test. */
    private class RecordingClient : TerminalViewClient {
        var tapToRaise = false
        var raiseCount = 0
        val tappedRows = mutableListOf<String>()
        val copiedRows = mutableListOf<String>()

        override fun onTerminalTapToRaiseKeyboard(): Boolean {
            if (!tapToRaise) return false
            raiseCount++
            return true
        }
        override fun isTapToRaiseKeyboardActive(): Boolean = tapToRaise
        override fun onTerminalRowTapped(text: String, isCurrentInput: Boolean) {
            tappedRows += text
        }
        override fun onTerminalRowCopyToInput(text: String, isCurrentInput: Boolean) {
            copiedRows += text
        }

        /** Fired the moment a confirmed single tap reaches the view, ahead of every gate. */
        var onConfirmedTap: () -> Unit = {}

        override fun onScale(scale: Float): Float = scale
        override fun onSingleTapUp(e: MotionEvent?) = onConfirmedTap()
        override fun shouldBackButtonBeMappedToEscape(): Boolean = false
        override fun shouldEnforceCharBasedInput(): Boolean = true
        override fun shouldUseCtrlSpaceWorkaround(): Boolean = false
        override fun isTerminalViewSelected(): Boolean = true
        override fun copyModeChanged(copyMode: Boolean) {}
        override fun onKeyDown(keyCode: Int, e: KeyEvent?, session: TerminalSession?): Boolean = false
        override fun onKeyUp(keyCode: Int, e: KeyEvent?): Boolean = false
        override fun onLongPress(event: MotionEvent?): Boolean = false
        override fun readControlKey(): Boolean = false
        override fun readAltKey(): Boolean = false
        override fun readShiftKey(): Boolean = false
        override fun readFnKey(): Boolean = false
        override fun onCodePoint(codePoint: Int, ctrlDown: Boolean, session: TerminalSession?): Boolean = false
        override fun onEmulatorSet() {}
        override fun logError(tag: String?, message: String?) {}
        override fun logWarn(tag: String?, message: String?) {}
        override fun logInfo(tag: String?, message: String?) {}
        override fun logDebug(tag: String?, message: String?) {}
        override fun logVerbose(tag: String?, message: String?) {}
        override fun logStackTraceWithMessage(tag: String?, message: String?, e: Exception?) {}
        override fun logStackTrace(tag: String?, e: Exception?) {}
    }
}
