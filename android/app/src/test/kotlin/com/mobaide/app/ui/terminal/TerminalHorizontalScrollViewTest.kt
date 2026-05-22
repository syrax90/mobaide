package com.mobaide.app.ui.terminal

import android.content.Context
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.HorizontalScrollView
import org.junit.Assert.assertEquals
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
 * [HorizontalScrollView] subclass that replaced a Compose `Modifier.horizontalScroll`
 * to fix vertical scroll being stolen by horizontal finger drift in the opened-session
 * terminal screen.
 *
 * The load-bearing behavior under test is the Full-display-mode passthrough: when the
 * inner [com.termux.view.TerminalView] fits the viewport (no horizontal overflow), both
 * `onInterceptTouchEvent` and `onTouchEvent` must short-circuit to `false` so events
 * flow straight through to the child View untouched. Without this, the HSV would consume
 * events in Full mode and break the vertical scroll behavior the bug fix is meant to
 * preserve.
 *
 * Robolectric SDK is pinned to 33 to match the rest of the JVM unit-test suite
 * (see `AgentListScreenTest`, `DeviceIdentityStoreTest`).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalHorizontalScrollViewTest {

    private lateinit var context: Context

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
    }

    @Test
    fun init_setsHorizontalScrollViewInteropDefaults() {
        val hsv = TerminalHorizontalScrollView(context)

        assertTrue(
            "isFillViewport must be true so the inner TerminalView stretches vertically when its LayoutParams.height = MATCH_PARENT",
            hsv.isFillViewport,
        )
        assertFalse(
            "horizontal scroll bar must be disabled for clean terminal visuals",
            hsv.isHorizontalScrollBarEnabled,
        )
        assertEquals(
            "overScroll must be NEVER so a non-overflowing static-columns surface does not glow-edge",
            View.OVER_SCROLL_NEVER,
            hsv.overScrollMode,
        )
    }

    @Test
    fun onInterceptTouchEvent_returnsFalse_whenNoChildAttached() {
        val hsv = TerminalHorizontalScrollView(context)
        val event = obtainDownEvent()
        try {
            assertFalse(
                "with no child the HSV must never intercept — defensive branch for the brief AndroidView recreate window",
                hsv.onInterceptTouchEvent(event),
            )
        } finally {
            event.recycle()
        }
    }

    @Test
    fun onInterceptTouchEvent_returnsFalse_whenChildFitsViewport() {
        val hsv = layoutWithChild(parentWidth = 400, childWidth = 400)
        val event = obtainDownEvent()
        try {
            assertFalse(
                "Full display mode: child width == HSV width → no horizontal overflow → HSV must be a transparent passthrough so vertical scroll is not stolen",
                hsv.onInterceptTouchEvent(event),
            )
        } finally {
            event.recycle()
        }
    }

    @Test
    fun onTouchEvent_returnsFalse_whenNoChildAttached() {
        val hsv = TerminalHorizontalScrollView(context)
        val event = obtainDownEvent()
        try {
            assertFalse(
                "with no child the HSV must not consume touches — defensive branch mirroring the intercept side",
                hsv.onTouchEvent(event),
            )
        } finally {
            event.recycle()
        }
    }

    @Test
    fun onTouchEvent_returnsFalse_whenChildFitsViewport() {
        val hsv = layoutWithChild(parentWidth = 400, childWidth = 400)
        val event = obtainDownEvent()
        try {
            assertFalse(
                "Full display mode: HSV must not consume touch events when there is no horizontal content to pan, so the inner TerminalView keeps receiving them",
                hsv.onTouchEvent(event),
            )
        } finally {
            event.recycle()
        }
    }

    /**
     * Build a [TerminalHorizontalScrollView] containing a stub child View, then drive a
     * measure + layout pass so both views report real `width` to the production
     * `hasHorizontalOverflow()` check (`child.width > width`).
     */
    private fun layoutWithChild(parentWidth: Int, childWidth: Int): TerminalHorizontalScrollView {
        val hsv = TerminalHorizontalScrollView(context)
        val child = View(context).apply {
            layoutParams = ViewGroup.LayoutParams(childWidth, ViewGroup.LayoutParams.MATCH_PARENT)
        }
        hsv.addView(child)
        // Manually drive measure+layout. The Robolectric stand-in HSV's onLayout will
        // call child.layout with the measured child width; we measure-spec the parent
        // EXACTLY to make `hsv.width` deterministic.
        val widthSpec = View.MeasureSpec.makeMeasureSpec(parentWidth, View.MeasureSpec.EXACTLY)
        val heightSpec = View.MeasureSpec.makeMeasureSpec(200, View.MeasureSpec.EXACTLY)
        hsv.measure(widthSpec, heightSpec)
        hsv.layout(0, 0, parentWidth, 200)
        return hsv
    }

    private fun obtainDownEvent(): MotionEvent {
        val downTime = android.os.SystemClock.uptimeMillis()
        return MotionEvent.obtain(downTime, downTime, MotionEvent.ACTION_DOWN, 0f, 0f, 0)
    }
}
