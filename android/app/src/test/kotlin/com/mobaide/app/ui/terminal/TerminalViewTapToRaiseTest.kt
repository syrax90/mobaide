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
 * Robolectric tests for the two gates guarding input-row resolution in the forked
 * `TerminalView.onSingleTapUp`.
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

    /** Down+up at the top-left text row, then idle past the double-tap timeout so the
     *  gesture recognizer delivers the confirmed single tap. */
    private fun singleTapTopRow() {
        val downTime = SystemClock.uptimeMillis()
        val down = MotionEvent.obtain(downTime, downTime, MotionEvent.ACTION_DOWN, 10f, 1f, 0)
        val up = MotionEvent.obtain(downTime, downTime + 50, MotionEvent.ACTION_UP, 10f, 1f, 0)
        view.onTouchEvent(down)
        view.onTouchEvent(up)
        down.recycle()
        up.recycle()
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(1_000))
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

    private fun recordSentBytes(): StringBuilder {
        val sentBytes = StringBuilder()
        session.setOnInputListener { data, offset, count ->
            sentBytes.append(String(data, offset, count, Charsets.ISO_8859_1))
        }
        return sentBytes
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

    private companion object {
        /** DECSET 1049 — switch to the alternate screen buffer, clearing it. */
        const val ALTERNATE_SCREEN_ON = "[?1049h"

        /** DECSET 1000 — X11 press/release mouse reporting. Does not switch buffers. */
        const val MOUSE_TRACKING_ON = "[?1000h"
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

        override fun onScale(scale: Float): Float = scale
        override fun onSingleTapUp(e: MotionEvent?) {}
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
