package com.mobaide.app.ui.terminal

import android.content.Context
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.ViewGroup
import android.widget.HorizontalScrollView
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * JVM unit tests for [TerminalHorizontalScrollView] — the small native
 * [HorizontalScrollView] subclass that wraps the vendored [com.termux.view.TerminalView] to
 * provide horizontal panning in Static display mode without stealing vertical scroll.
 *
 * Two load-bearing behaviors are under test:
 *
 *  - **Full-mode passthrough**: when the inner view fits the viewport (no horizontal
 *    overflow) both `onInterceptTouchEvent` and `onTouchEvent` must short-circuit to
 *    `false` so events flow straight to the child View untouched.
 *  - **Direction-aware intercept in Static mode**: when the child overflows the viewport,
 *    intercept *only* on horizontally-dominant motion past
 *    [ViewConfiguration.getScaledTouchSlop]. Vertical-dominant moves stay with the child so
 *    terminal scrollback keeps working.
 *
 * The fixture deliberately uses [View.setMinimumWidth] (not a plain stub) so the test child
 * actually reports `measuredWidth = childWidth` when the HSV passes `UNSPECIFIED` — without
 * that, AOSP HSV's `fillViewport=true` collapses any plain-View child to the parent width
 * and the overflow path is never exercised. Production code achieves the same outcome via
 * the `TerminalView.onMeasure` override.
 *
 * Robolectric SDK pinned to 33 to match the rest of the JVM unit-test suite (see
 * `AgentListScreenTest`, `DeviceIdentityStoreTest`).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalHorizontalScrollViewTest {

    private lateinit var context: Context
    private var touchSlop: Int = 0

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        touchSlop = ViewConfiguration.get(context).scaledTouchSlop
    }

    @Test
    fun onInterceptTouchEvent_returnsFalse_whenChildFitsViewport() {
        val hsv = layoutWithChild(parentWidth = 400, childWidth = 400)
        obtainDownEvent().use { ev ->
            assertFalse(
                "Full display mode: child width == HSV width → no horizontal overflow → HSV must be a transparent passthrough so vertical scroll is not stolen",
                hsv.onInterceptTouchEvent(ev),
            )
        }
    }

    @Test
    fun onTouchEvent_returnsFalse_whenChildFitsViewport() {
        val hsv = layoutWithChild(parentWidth = 400, childWidth = 400)
        obtainDownEvent().use { ev ->
            assertFalse(
                "Full display mode: HSV must not consume touch events when there is no horizontal content to pan, so the inner TerminalView keeps receiving them",
                hsv.onTouchEvent(ev),
            )
        }
    }

    @Test
    fun onInterceptTouchEvent_returnsFalse_onActionDown_whenChildOverflows() {
        val hsv = layoutWithChild(parentWidth = 400, childWidth = 800)
        obtainDownEvent().use { ev ->
            assertFalse(
                "ACTION_DOWN alone has no direction — must never claim on DOWN even when overflowing, so the child sees the start of the gesture",
                hsv.onInterceptTouchEvent(ev),
            )
        }
    }

    @Test
    fun onInterceptTouchEvent_intercepts_onHorizontallyDominantMoveAboveSlop() {
        val hsv = layoutWithChild(parentWidth = 400, childWidth = 800)
        playGesture(hsv) { down, move ->
            down(0f, 0f)
            assertTrue(
                "horizontally-dominant move past touchSlop in an overflowing HSV must intercept so native HSV scrolling can take over",
                move(slopExceedingHorizontalOffset(), slopFractionVerticalOffset()),
            )
        }
    }

    @Test
    fun onInterceptTouchEvent_doesNotIntercept_onVerticallyDominantMove() {
        val hsv = layoutWithChild(parentWidth = 400, childWidth = 800)
        playGesture(hsv) { down, move ->
            down(0f, 0f)
            assertFalse(
                "vertical-dominant motion must stay with the child so terminal scrollback keeps working even in overflowing Static mode",
                move(slopFractionHorizontalOffset(), slopExceedingVerticalOffset()),
            )
        }
    }

    @Test
    fun onInterceptTouchEvent_doesNotIntercept_onMoveBelowSlop() {
        val hsv = layoutWithChild(parentWidth = 400, childWidth = 800)
        playGesture(hsv) { down, move ->
            down(0f, 0f)
            assertFalse(
                "sub-slop motion must not be interpreted as a pan — short presses or jitter should not start scrolling",
                move(slopFractionHorizontalOffset(), slopFractionVerticalOffset()),
            )
        }
    }

    @Test
    fun onInterceptTouchEvent_staysLatched_onSubsequentVerticalMove_afterHorizontalLatch() {
        val hsv = layoutWithChild(parentWidth = 400, childWidth = 800)
        playGesture(hsv) { down, move ->
            down(0f, 0f)
            assertTrue("first horizontal-dominant move latches the intercept", move(slopExceedingHorizontalOffset(), slopFractionVerticalOffset()))
            assertTrue(
                "once horizontal pan is in progress, subsequent vertical drift must not abort it — matches native HSV behavior and avoids ping-pong mid-stroke",
                move(slopExceedingHorizontalOffset() + 10f, slopExceedingVerticalOffset()),
            )
        }
    }

    @Test
    fun onInterceptTouchEvent_resetsLatch_onActionUp() {
        val hsv = layoutWithChild(parentWidth = 400, childWidth = 800)
        playGesture(hsv) { down, move ->
            down(0f, 0f)
            assertTrue(move(slopExceedingHorizontalOffset(), slopFractionVerticalOffset()))
        }
        playGesture(hsv) { down, move ->
            down(0f, 0f)
            assertFalse(
                "after ACTION_UP a fresh gesture starts with a fresh decision — vertical-dominant follow-up must not inherit the prior horizontal latch",
                move(slopFractionHorizontalOffset(), slopExceedingVerticalOffset()),
            )
        }
    }

    @Test
    fun onInterceptTouchEvent_resetsLatch_onActionCancel() {
        val hsv = layoutWithChild(parentWidth = 400, childWidth = 800)
        playGesture(hsv, endAction = MotionEvent.ACTION_CANCEL) { down, move ->
            down(0f, 0f)
            assertTrue(
                "baseline: horizontal-dominant move latches the intercept before the CANCEL terminator fires",
                move(slopExceedingHorizontalOffset(), slopFractionVerticalOffset()),
            )
        }
        playGesture(hsv) { down, move ->
            down(0f, 0f)
            assertFalse(
                "after ACTION_CANCEL the latch must reset just like ACTION_UP — otherwise a subsequent vertical gesture would be stolen because the prior horizontal claim leaked across gestures",
                move(slopFractionHorizontalOffset(), slopExceedingVerticalOffset()),
            )
        }
    }

    @Test
    fun onInterceptTouchEvent_doesNotIntercept_onEqualMagnitudeDiagonal_aboveSlop() {
        val hsv = layoutWithChild(parentWidth = 400, childWidth = 800)
        val equalOffset = slopExceedingHorizontalOffset()
        playGesture(hsv) { down, move ->
            down(0f, 0f)
            assertFalse(
                "dominance check is strict (dx > dy) — when |dx| == |dy| the gesture is ambiguous and must be left to the child, mirroring the child's own |distanceY| >= |distanceX| disallow contract",
                move(equalOffset, equalOffset),
            )
        }
    }

    private fun slopExceedingHorizontalOffset(): Float = (touchSlop + 16).toFloat()
    private fun slopExceedingVerticalOffset(): Float = (touchSlop + 16).toFloat()
    private fun slopFractionHorizontalOffset(): Float = (touchSlop / 4).toFloat()
    private fun slopFractionVerticalOffset(): Float = (touchSlop / 4).toFloat()

    /**
     * Build a [TerminalHorizontalScrollView] containing a stub child View, then drive a
     * measure + layout pass so both views report real `width` to the production
     * `hasHorizontalOverflow()` check (`child.width > width`).
     *
     * The child uses [View.setMinimumWidth] so its `onMeasure` returns `childWidth` even when
     * the HSV passes `UNSPECIFIED` — mirrors what `TerminalView.onMeasure` does in production.
     * Without this, AOSP HSV's `fillViewport=true` would collapse the child to parent width and
     * the overflow path would never be exercised.
     */
    private fun layoutWithChild(parentWidth: Int, childWidth: Int): TerminalHorizontalScrollView {
        val hsv = TerminalHorizontalScrollView(context)
        val child = View(context).apply {
            layoutParams = ViewGroup.LayoutParams(childWidth, ViewGroup.LayoutParams.MATCH_PARENT)
            minimumWidth = childWidth
        }
        hsv.addView(child)
        val widthSpec = View.MeasureSpec.makeMeasureSpec(parentWidth, View.MeasureSpec.EXACTLY)
        val heightSpec = View.MeasureSpec.makeMeasureSpec(200, View.MeasureSpec.EXACTLY)
        hsv.measure(widthSpec, heightSpec)
        hsv.layout(0, 0, parentWidth, 200)
        return hsv
    }

    /**
     * Drive a synthetic touch gesture through the HSV's intercept path with the same `downTime`
     * across all events. The block receives a `down(x, y)` action and a `move(x, y)` action
     * (which returns whether the MOVE was intercepted); after the block returns the helper
     * synthesizes a terminating event so per-gesture state is properly reset between calls.
     *
     * [endAction] is the terminator dispatched after the block — defaults to [MotionEvent.ACTION_UP]
     * (normal lift-off). Pass [MotionEvent.ACTION_CANCEL] to test the cancel-driven latch reset.
     */
    private fun playGesture(
        hsv: TerminalHorizontalScrollView,
        endAction: Int = MotionEvent.ACTION_UP,
        block: (down: (Float, Float) -> Boolean, move: (Float, Float) -> Boolean) -> Unit,
    ) {
        val downTime = SystemClock.uptimeMillis()
        var lastEventTime = downTime
        val down: (Float, Float) -> Boolean = { x, y ->
            val ev = MotionEvent.obtain(downTime, downTime, MotionEvent.ACTION_DOWN, x, y, 0)
            try { hsv.onInterceptTouchEvent(ev) } finally { ev.recycle() }
        }
        val move: (Float, Float) -> Boolean = { x, y ->
            lastEventTime += 16
            val ev = MotionEvent.obtain(downTime, lastEventTime, MotionEvent.ACTION_MOVE, x, y, 0)
            try { hsv.onInterceptTouchEvent(ev) } finally { ev.recycle() }
        }
        try {
            block(down, move)
        } finally {
            lastEventTime += 16
            val terminator = MotionEvent.obtain(downTime, lastEventTime, endAction, 0f, 0f, 0)
            try { hsv.onInterceptTouchEvent(terminator) } finally { terminator.recycle() }
        }
    }

    private fun obtainDownEvent(): MotionEvent {
        val downTime = SystemClock.uptimeMillis()
        return MotionEvent.obtain(downTime, downTime, MotionEvent.ACTION_DOWN, 0f, 0f, 0)
    }

    private inline fun MotionEvent.use(block: (MotionEvent) -> Unit) {
        try { block(this) } finally { recycle() }
    }
}
