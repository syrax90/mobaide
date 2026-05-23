package com.mobaide.app.ui.terminal

import android.content.Context
import android.view.View
import android.view.ViewGroup
import android.widget.HorizontalScrollView
import com.termux.view.TerminalView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * JVM unit tests for the [TerminalView.onMeasure] override.
 *
 * Pins the contract that broke horizontal panning in Static display mode: when the parent
 * ([HorizontalScrollView] in production) gives `TerminalView` an `UNSPECIFIED` width spec,
 * the View must report its requested `layoutParams.width` as the measured width — not 0.
 * Without this, the default `View.onMeasure` returns 0 for UNSPECIFIED (no background, no
 * minimumWidth), and `HorizontalScrollView.fillViewport=true` then collapses the surface to
 * viewport width, defeating horizontal panning entirely.
 *
 * Robolectric SDK pinned to 33 to match the rest of the JVM unit-test suite.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalViewMeasureTest {

    private lateinit var context: Context

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
    }

    @Test
    fun onMeasure_unspecifiedWidth_usesLayoutParamsWidth_whenSet() {
        val view = TerminalView(context, null).apply {
            layoutParams = ViewGroup.LayoutParams(1234, ViewGroup.LayoutParams.MATCH_PARENT)
        }

        view.measure(
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
            View.MeasureSpec.makeMeasureSpec(800, View.MeasureSpec.EXACTLY),
        )

        assertEquals(
            "Under UNSPECIFIED width, the View must use lp.width — otherwise HSV.fillViewport collapses the Static-mode surface to viewport width and breaks horizontal pan",
            1234,
            view.measuredWidth,
        )
    }

    @Test
    fun onMeasure_unspecifiedWidth_fallsBackToMinimumWidth_whenLayoutParamsWidthIsMatchParent() {
        val view = TerminalView(context, null).apply {
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
        }

        view.measure(
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
            View.MeasureSpec.makeMeasureSpec(800, View.MeasureSpec.EXACTLY),
        )

        assertEquals(
            "MATCH_PARENT under UNSPECIFIED has no concrete size — default to getSuggestedMinimumWidth (0 without a background) so HSV.fillViewport can take over",
            0,
            view.measuredWidth,
        )
    }

    @Test
    fun onMeasure_exactlyWidth_usesSpecSize_regardlessOfLayoutParamsWidth() {
        val view = TerminalView(context, null).apply {
            layoutParams = ViewGroup.LayoutParams(9999, ViewGroup.LayoutParams.MATCH_PARENT)
        }

        view.measure(
            View.MeasureSpec.makeMeasureSpec(640, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(800, View.MeasureSpec.EXACTLY),
        )

        assertEquals(
            "EXACTLY width must win over lp.width — preserves the Compose AndroidView path that explicitly constrains the View to a measured size",
            640,
            view.measuredWidth,
        )
    }

    @Test
    fun onMeasure_unspecifiedWidth_fallsBackToSuggestedMinimum_whenLayoutParamsIsNull() {
        val view = TerminalView(context, null)
        assertEquals(
            "precondition: a freshly constructed TerminalView has no layoutParams until attached to a parent",
            null,
            view.layoutParams,
        )

        view.measure(
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
            View.MeasureSpec.makeMeasureSpec(800, View.MeasureSpec.EXACTLY),
        )

        assertEquals(
            "with no layoutParams the lp guard must short-circuit safely (no NPE) and fall back to getSuggestedMinimumWidth — protects the orphaned-view path where Compose's AndroidView holder measures the View before attaching it",
            0,
            view.measuredWidth,
        )
    }

    @Test
    fun onMeasure_atMostWidth_capsAtSpecSize_regardlessOfLayoutParamsWidth() {
        val view = TerminalView(context, null).apply {
            layoutParams = ViewGroup.LayoutParams(9999, ViewGroup.LayoutParams.MATCH_PARENT)
        }

        view.measure(
            View.MeasureSpec.makeMeasureSpec(480, View.MeasureSpec.AT_MOST),
            View.MeasureSpec.makeMeasureSpec(800, View.MeasureSpec.EXACTLY),
        )

        assertEquals(
            "AT_MOST width must yield specSize per View.getDefaultSize (AT_MOST and EXACTLY both return specSize unconditionally — the minimumWidth argument is only consulted for UNSPECIFIED). Pins that the non-UNSPECIFIED branch defers to default behavior and lp.width is correctly ignored.",
            480,
            view.measuredWidth,
        )
    }

    /**
     * End-to-end regression: place a [TerminalView] inside our production
     * [TerminalHorizontalScrollView] wrapper, set lp.width via the same code path Static mode
     * uses (we mimic the result with a direct setLayoutParams since calling setTextSize
     * here would create a renderer without an attached emulator), then drive measure+layout
     * and assert the child width exceeds the parent width — i.e. `hasHorizontalOverflow()`
     * actually triggers and HSV will be able to pan.
     */
    @Test
    fun horizontalScrollView_seesHorizontalOverflow_whenTerminalViewHasFixedLayoutWidth() {
        val hsv = TerminalHorizontalScrollView(context)
        val terminalView = TerminalView(context, null).apply {
            layoutParams = ViewGroup.LayoutParams(800, ViewGroup.LayoutParams.MATCH_PARENT)
        }
        hsv.addView(terminalView)

        val widthSpec = View.MeasureSpec.makeMeasureSpec(400, View.MeasureSpec.EXACTLY)
        val heightSpec = View.MeasureSpec.makeMeasureSpec(200, View.MeasureSpec.EXACTLY)
        hsv.measure(widthSpec, heightSpec)
        hsv.layout(0, 0, 400, 200)

        assertEquals(
            "TerminalView under an HSV parent (UNSPECIFIED width spec) must measure to its lp.width — scrolling-fix v2 contract",
            800,
            terminalView.width,
        )
        assertTrue(
            "After layout, child.width (800) must exceed hsv.width (400) — this is the load-bearing condition the HSV checks to decide whether to intercept for horizontal panning",
            terminalView.width > hsv.width,
        )
    }
}
