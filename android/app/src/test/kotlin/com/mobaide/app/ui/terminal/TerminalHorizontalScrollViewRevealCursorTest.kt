package com.mobaide.app.ui.terminal

import android.content.Context
import android.os.SystemClock
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.ViewGroup
import com.termux.terminal.TerminalSession
import com.termux.view.TerminalView
import com.termux.view.TerminalViewClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * [TerminalHorizontalScrollView.revealCursor] against a real Static-mode [TerminalView]
 * (120 forced columns inside a 400 px viewport) and a real emulator, so the cursor's pixel
 * position comes from the same renderer metrics production uses.
 *
 * Only the offset may change: the surface width (and so the PTY grid) is asserted untouched
 * wherever a pan happens. Native graphics give the renderer a real glyph width — under the
 * legacy shadow the font width is 0 and `revealCursor` would bail on its metrics guard.
 * SDK pinned to 33 to match the rest of the JVM/Robolectric suite.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class TerminalHorizontalScrollViewRevealCursorTest {

    private lateinit var context: Context
    private lateinit var session: TerminalSession
    private var now = 50_000L
    private val reveal = TerminalCursorReveal(clock = { now })

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        session = TerminalSession(TerminalSessionCallback())
    }

    @Test
    fun typingPastTheRightEdge_pansSoTheCursorSitsAQuarterViewportInside() {
        val (hsv, view) = staticSurface()
        val widthBefore = view.width
        armAtCursor(view)

        feed("x".repeat(60))
        hsv.revealCursor(view, reveal)

        assertTrue("col 60 starts beyond a 400 px viewport", cursorLeft(view) > VIEWPORT)
        assertCursorOnScreen(hsv, view)
        assertEquals(
            "lookahead: the cursor's right edge lands at 3/4 of the viewport, not flush with the edge",
            VIEWPORT * 3 / 4f,
            cursorRight(view) - hsv.scrollX,
            1f,
        )
        assertEquals("only the offset moves — a width change would resize the PTY", widthBefore, view.width)
    }

    @Test
    fun carriageReturnAfterAPan_returnsToTheLeftEdge() {
        val (hsv, view) = staticSurface()
        armAtCursor(view)
        feed("x".repeat(60))
        hsv.revealCursor(view, reveal)
        assertTrue("precondition: the first reveal panned right", hsv.scrollX > 0)

        feed("\r\n")
        hsv.revealCursor(view, reveal)

        assertEquals(0, hsv.scrollX)
    }

    @Test
    fun returningToTheBurstOriginAfterAPan_pansBack() {
        val (hsv, view) = staticSurface()
        armAtCursor(view)
        feed("x".repeat(60))
        hsv.revealCursor(view, reveal)
        assertTrue("precondition: the first reveal panned right", hsv.scrollX > 0)

        feed("\r")
        assertEquals("precondition: the cursor is back at the origin cell", 0, requireNotNull(view.mEmulator).cursorCol)
        hsv.revealCursor(view, reveal)

        assertEquals("Home / Ctrl-U to the origin must not strand the view at the old column", 0, hsv.scrollX)
    }

    @Test
    fun synchronizedOutputFreeze_holdsTheRevealUntilTheFrameCompletes() {
        val (hsv, view) = staticSurface()
        armAtCursor(view)

        feed(SYNC_ON + "x".repeat(60))
        assertTrue("precondition: mode 2026 is set", requireNotNull(view.mEmulator).isSynchronizedOutputActive)
        hsv.revealCursor(view, reveal)
        assertEquals("a mid-frame cursor is not where the finished frame will put it", 0, hsv.scrollX)

        feed(SYNC_OFF)
        hsv.revealCursor(view, reveal)
        assertCursorOnScreen(hsv, view)
    }

    @Test
    fun outputWithoutAKeystroke_neverPans() {
        val (hsv, view) = staticSurface()

        feed("x".repeat(60))
        hsv.revealCursor(view, reveal)

        assertEquals("passive output must not move the user's horizontal position", 0, hsv.scrollX)
    }

    @Test
    fun outputAfterTheWindowLapses_neverPans() {
        val (hsv, view) = staticSurface()
        armAtCursor(view)
        now += TerminalCursorReveal.WINDOW_MS

        feed("x".repeat(60))
        hsv.revealCursor(view, reveal)

        assertEquals(0, hsv.scrollX)
    }

    @Test
    fun aCursorThatHasNotMovedSinceTheKeystroke_neverPans() {
        val (hsv, view) = staticSurface()
        feed("x".repeat(60))
        armAtCursor(view)

        hsv.revealCursor(view, reveal)

        assertEquals(
            "a TUI that parks its cursor off screen would otherwise drag the view there on every key",
            0,
            hsv.scrollX,
        )
    }

    @Test
    fun fullMode_isANoOp() {
        val (hsv, view) = fullSurface()
        armAtCursor(view)

        feed("x".repeat(20))
        hsv.revealCursor(view, reveal)

        assertEquals("no overflow, nothing to pan", 0, hsv.scrollX)
    }

    @Test
    fun aFingerOnTheSurface_holdsTheRevealUntilLift() {
        val (hsv, view) = staticSurface()
        armAtCursor(view)
        feed("x".repeat(60))
        val downTime = SystemClock.uptimeMillis()

        dispatch(hsv, downTime, MotionEvent.ACTION_DOWN, 50f, 50f)
        hsv.revealCursor(view, reveal)
        assertEquals("a pan under the user's finger would fight their gesture", 0, hsv.scrollX)

        dispatch(hsv, downTime, MotionEvent.ACTION_UP, 50f, 50f)
        hsv.revealCursor(view, reveal)
        assertCursorOnScreen(hsv, view)
    }

    /**
     * A vertical scrollback drag is the terminal's: after its touch slop the [TerminalView]
     * calls `requestDisallowInterceptTouchEvent(true)`, so the rest of the gesture — the lift
     * included — is dispatched straight to the child and never reaches the HSV's
     * intercept or touch handlers. Once the finger is up nothing is touching the surface,
     * so typing afterwards must still follow the cursor.
     */
    @Test
    fun afterAVerticalScrollbackDragEnds_typingStillReveals() {
        val (hsv, view) = staticSurface()
        val downTime = SystemClock.uptimeMillis()
        dispatch(hsv, downTime, MotionEvent.ACTION_DOWN, 100f, 100f)
        view.parent.requestDisallowInterceptTouchEvent(true)
        dispatch(hsv, downTime, MotionEvent.ACTION_MOVE, 100f, 300f)
        dispatch(hsv, downTime, MotionEvent.ACTION_UP, 100f, 300f)

        armAtCursor(view)
        feed("x".repeat(60))
        hsv.revealCursor(view, reveal)

        assertCursorOnScreen(hsv, view)
    }

    @Test
    fun claimingAHorizontalDrag_firesTheDragStartHookOnce() {
        val (hsv, _) = staticSurface()
        var starts = 0
        hsv.onHorizontalDragStart = { starts++ }
        val slop = ViewConfiguration.get(context).scaledTouchSlop
        val downTime = SystemClock.uptimeMillis()

        intercept(hsv, downTime, MotionEvent.ACTION_DOWN, 200f, 100f)
        intercept(hsv, downTime, MotionEvent.ACTION_MOVE, 200f - slop - 16, 100f)
        intercept(hsv, downTime, MotionEvent.ACTION_MOVE, 200f - slop - 40, 100f)
        intercept(hsv, downTime, MotionEvent.ACTION_UP, 200f - slop - 40, 100f)

        assertEquals("the user's pan disarms the reveal once, when it is claimed", 1, starts)
    }

    @Test
    fun aVerticalDrag_neverFiresTheDragStartHook() {
        val (hsv, _) = staticSurface()
        var starts = 0
        hsv.onHorizontalDragStart = { starts++ }
        val slop = ViewConfiguration.get(context).scaledTouchSlop
        val downTime = SystemClock.uptimeMillis()

        intercept(hsv, downTime, MotionEvent.ACTION_DOWN, 200f, 100f)
        intercept(hsv, downTime, MotionEvent.ACTION_MOVE, 205f, 100f + slop + 40)
        intercept(hsv, downTime, MotionEvent.ACTION_UP, 205f, 100f + slop + 40)

        assertEquals("scrollback drags leave a pending reveal alone", 0, starts)
    }

    private fun staticSurface(): Pair<TerminalHorizontalScrollView, TerminalView> =
        surface(forcedColumns = STATIC_COLUMNS).also { (hsv, view) ->
            assertTrue(
                "precondition: the Static surface overflows the viewport",
                view.width > hsv.width,
            )
            assertEquals(STATIC_COLUMNS, requireNotNull(view.mEmulator).mColumns)
        }

    private fun fullSurface(): Pair<TerminalHorizontalScrollView, TerminalView> =
        surface(forcedColumns = 0).also { (hsv, view) ->
            assertEquals("precondition: Full mode fits the viewport", hsv.width, view.width)
        }

    private fun surface(forcedColumns: Int): Pair<TerminalHorizontalScrollView, TerminalView> {
        val hsv = TerminalHorizontalScrollView(context)
        val view = TerminalView(context, null).apply {
            setTerminalViewClient(StubViewClient())
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
            setTextSize(TEXT_SIZE)
            setForcedColumns(forcedColumns)
            attachSession(session)
        }
        hsv.addView(view)
        hsv.measure(
            View.MeasureSpec.makeMeasureSpec(VIEWPORT, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(HEIGHT, View.MeasureSpec.EXACTLY),
        )
        hsv.layout(0, 0, VIEWPORT, HEIGHT)
        assertTrue("precondition: renderer metrics are real", view.rendererFontWidth > 0f)
        return hsv to view
    }

    private fun armAtCursor(view: TerminalView) {
        val emulator = requireNotNull(view.mEmulator)
        reveal.arm(emulator.cursorCol, emulator.cursorRow)
    }

    private fun feed(text: String) {
        val bytes = text.toByteArray(Charsets.UTF_8)
        requireNotNull(session.emulator).append(bytes, bytes.size)
    }

    private fun cursorLeft(view: TerminalView): Float =
        view.getPointX(requireNotNull(view.mEmulator).cursorCol).toFloat()

    private fun cursorRight(view: TerminalView): Float = cursorLeft(view) + view.rendererFontWidth

    private fun assertCursorOnScreen(hsv: TerminalHorizontalScrollView, view: TerminalView) {
        val left = cursorLeft(view)
        val right = cursorRight(view)
        assertTrue(
            "cursor cell [$left, $right] must sit inside the visible window [${hsv.scrollX}, ${hsv.scrollX + hsv.width}]",
            left >= hsv.scrollX && right <= hsv.scrollX + hsv.width,
        )
    }

    private fun intercept(hsv: TerminalHorizontalScrollView, downTime: Long, action: Int, x: Float, y: Float) {
        val ev = MotionEvent.obtain(downTime, downTime + 16, action, x, y, 0)
        try { hsv.onInterceptTouchEvent(ev) } finally { ev.recycle() }
    }

    private fun dispatch(hsv: TerminalHorizontalScrollView, downTime: Long, action: Int, x: Float, y: Float) {
        val ev = MotionEvent.obtain(downTime, downTime + 16, action, x, y, 0)
        try { hsv.dispatchTouchEvent(ev) } finally { ev.recycle() }
    }

    private class StubViewClient : TerminalViewClient {
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

    private companion object {
        const val VIEWPORT = 400
        const val HEIGHT = 800
        const val TEXT_SIZE = 24
        const val STATIC_COLUMNS = 120
        const val SYNC_ON = "\u001b[?2026h"
        const val SYNC_OFF = "\u001b[?2026l"
    }
}
