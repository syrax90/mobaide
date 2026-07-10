package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.layout
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.LayoutDirection
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * JVM tests for [imeCursorCounterPan] — the cursor-following counter-shift that keeps a
 * top-anchored TUI's input line visible while [imeFloatingPan] pans the whole column up:
 *   - `dy = clamp(height − anchorBottomPx, 0, pan)`: zero for a bottom-anchored cursor
 *     (net full pan, the pre-existing behavior), the full pan for a near-top cursor
 *     (net zero pan), and in between the anchor row pins to the panned viewport bottom.
 *   - Placement-only: inset AND cursor-anchor changes re-run placement but never
 *     remeasure, so the no-resize contract of [imeFloatingPan] carries over.
 *   - The `pan == 0` guard keeps the anchor state out of the snapshot dependency set
 *     while the IME is hidden, so per-output-frame cursor writes are free then.
 *
 * Insets are fixed-px fakes (the [WindowInsets] factory for static cases, a
 * snapshot-state-backed fake for the live-change case) and the counter-panned surface
 * gets an explicit px height, so assertions are density-independent. Robolectric SDK
 * pinned to 33 to match the rest of the JVM unit-test suite.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ImeCursorCounterPanTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    /**
     * Bottom inset backed by snapshot state: [getBottom] reads [bottomPx] inside the
     * placement pass, exactly like the real `WindowInsets.ime`, so mutating it
     * exercises the same placement-only invalidation path production hits when the
     * keyboard animates.
     */
    private class MutableBottomInsets(initialBottomPx: Int) : WindowInsets {
        var bottomPx by mutableIntStateOf(initialBottomPx)

        override fun getBottom(density: Density): Int = bottomPx
        override fun getTop(density: Density): Int = 0
        override fun getLeft(density: Density, layoutDirection: LayoutDirection): Int = 0
        override fun getRight(density: Density, layoutDirection: LayoutDirection): Int = 0
    }

    private class LayoutProbe {
        var measurePasses = 0
        var lastSize = IntSize.Zero
    }

    /** Counter-panned surface alone: any movement observed is the counter-shift `dy`. */
    private fun setCounterPannedContent(
        ime: WindowInsets,
        systemBars: WindowInsets = WindowInsets(bottom = NAV_BAR_PX),
        probe: LayoutProbe? = null,
        anchorBottomPx: () -> Int,
    ) {
        composeRule.setContent {
            val heightDp = with(LocalDensity.current) { TERMINAL_HEIGHT_PX.toDp() }
            Box(Modifier.fillMaxSize().testTag(HOST_TAG)) {
                Box(
                    Modifier
                        .imeCursorCounterPan(ime, systemBars, anchorBottomPx)
                        .layout { measurable, constraints ->
                            probe?.let { it.measurePasses++ }
                            val placeable = measurable.measure(constraints)
                            layout(placeable.width, placeable.height) { placeable.place(0, 0) }
                        }
                        .fillMaxWidth()
                        .height(heightDp)
                        .onSizeChanged { probe?.lastSize = it }
                        .testTag(PANNED_TAG),
                )
            }
        }
        composeRule.waitForIdle()
    }

    /**
     * Both modifiers stacked exactly as composed in TerminalScreen — [imeFloatingPan]
     * on the ancestor, [imeCursorCounterPan] on the surface — so the observed movement
     * is the net pan `dy − pan`.
     */
    private fun setStackedContent(ime: WindowInsets, anchorBottomPx: () -> Int) {
        val systemBars = WindowInsets(bottom = NAV_BAR_PX)
        composeRule.setContent {
            val heightDp = with(LocalDensity.current) { TERMINAL_HEIGHT_PX.toDp() }
            Box(Modifier.fillMaxSize().testTag(HOST_TAG)) {
                Box(Modifier.fillMaxSize().imeFloatingPan(ime, systemBars)) {
                    Box(
                        Modifier
                            .imeCursorCounterPan(ime, systemBars, anchorBottomPx)
                            .fillMaxWidth()
                            .height(heightDp)
                            .testTag(PANNED_TAG),
                    )
                }
            }
        }
        composeRule.waitForIdle()
    }

    private fun topInRootPx(tag: String): Float =
        composeRule.onNodeWithTag(tag).fetchSemanticsNode().positionInRoot.y

    @Test
    fun imeHidden_midAnchor_contentIsNotCounterShifted() {
        setCounterPannedContent(ime = WindowInsets(bottom = 0)) { ANCHOR_MID_PX }

        assertEquals(
            "With the IME hidden the counter-shift must clamp to the zero pan — a mid-screen cursor must not drag the resting terminal downward",
            topInRootPx(HOST_TAG),
            topInRootPx(PANNED_TAG),
            0f,
        )
    }

    @Test
    fun imeShorterThanSystemBars_contentIsNotCounterShifted() {
        setCounterPannedContent(ime = WindowInsets(bottom = NAV_BAR_PX / 2)) { ANCHOR_MID_PX }

        assertEquals(
            "When the IME inset is smaller than the system bars (dismiss-animation tail) the pan must coerce to zero before it reaches coerceIn — an unclamped negative pan would feed coerceIn an empty range",
            topInRootPx(HOST_TAG),
            topInRootPx(PANNED_TAG),
            0f,
        )
    }

    @Test
    fun defaultMaxAnchor_stacked_keepsFullUpwardPan() {
        setStackedContent(ime = WindowInsets(bottom = IME_PX)) { Int.MAX_VALUE }

        assertEquals(
            "Before any cursor report (Int.MAX_VALUE sentinel) the counter-shift must be zero, preserving the plain full ime − systemBars pan",
            topInRootPx(HOST_TAG) - PAN_PX,
            topInRootPx(PANNED_TAG),
            0f,
        )
    }

    @Test
    fun anchorAtOrBeyondSurfaceBottom_noCounterShift() {
        val anchor = mutableIntStateOf(TERMINAL_HEIGHT_PX)
        setCounterPannedContent(ime = WindowInsets(bottom = IME_PX)) { anchor.intValue }

        assertEquals(
            "Cursor row already at the surface bottom (bottom-anchored TUI) must yield a zero counter-shift — pixel-identical to the pre-existing full-pan behavior",
            topInRootPx(HOST_TAG),
            topInRootPx(PANNED_TAG),
            0f,
        )

        composeRule.runOnIdle { anchor.intValue = TERMINAL_HEIGHT_PX + 250 }
        composeRule.waitForIdle()

        assertEquals(
            "An anchor beyond the measured height (scrolled-back viewport) must clamp the counter-shift at zero, never pan the content upward",
            topInRootPx(HOST_TAG),
            topInRootPx(PANNED_TAG),
            0f,
        )
    }

    @Test
    fun anchorNearTop_stacked_netPanIsZero() {
        setStackedContent(ime = WindowInsets(bottom = IME_PX)) { ANCHOR_NEAR_TOP_PX }

        assertEquals(
            "A near-top cursor (fresh shell prompt, top-anchored TUI) must counter-shift by the full pan so the net movement is zero and the input line stays where it was",
            topInRootPx(HOST_TAG),
            topInRootPx(PANNED_TAG),
            0f,
        )
    }

    @Test
    fun midAnchor_stacked_pinsAnchorToPannedViewportBottom() {
        setStackedContent(ime = WindowInsets(bottom = IME_PX)) { ANCHOR_MID_PX }

        assertEquals(
            "A mid-screen cursor must land exactly on the panned viewport's bottom edge — pinned just above the InputBar riding the IME",
            topInRootPx(HOST_TAG) + TERMINAL_HEIGHT_PX - PAN_PX,
            topInRootPx(PANNED_TAG) + ANCHOR_MID_PX,
            0f,
        )
    }

    @Test
    fun imeInsetChange_rerunsPlacementOnly_sizeAndMeasureCountUnchanged() {
        val ime = MutableBottomInsets(0)
        val probe = LayoutProbe()
        setCounterPannedContent(ime = ime, probe = probe) { ANCHOR_NEAR_TOP_PX }
        val baselineTop = topInRootPx(PANNED_TAG)
        val baselineMeasures = probe.measurePasses
        val baselineSize = probe.lastSize

        composeRule.runOnIdle { ime.bottomPx = IME_PX }
        composeRule.waitForIdle()

        assertEquals(
            "IME show with a near-top anchor must counter-shift the surface down by the full pan",
            baselineTop + PAN_PX,
            topInRootPx(PANNED_TAG),
            0f,
        )
        assertEquals(
            "IME show must re-run placement only — an extra measure pass means the terminal would reflow its grid and emit a resize frame to the remote PTY",
            baselineMeasures,
            probe.measurePasses,
        )
        assertEquals(
            "Counter-panned content size must be identical with the IME hidden vs shown — the no-resize contract",
            baselineSize,
            probe.lastSize,
        )
    }

    @Test
    fun anchorChange_whileImeVisible_rerunsPlacementOnly_sizeAndMeasureCountUnchanged() {
        val anchor = mutableIntStateOf(TERMINAL_HEIGHT_PX)
        val probe = LayoutProbe()
        setCounterPannedContent(ime = WindowInsets(bottom = IME_PX), probe = probe) { anchor.intValue }
        val baselineTop = topInRootPx(PANNED_TAG)
        val baselineMeasures = probe.measurePasses
        val baselineSize = probe.lastSize

        composeRule.runOnIdle { anchor.intValue = ANCHOR_MID_PX }
        composeRule.waitForIdle()

        assertEquals(
            "A cursor move while the IME is up must re-place the surface by the new height − anchor delta",
            baselineTop + (TERMINAL_HEIGHT_PX - ANCHOR_MID_PX),
            topInRootPx(PANNED_TAG),
            0f,
        )
        assertEquals(
            "A cursor-anchor change must re-run placement only — a measure pass here would resize the grid on every cursor report",
            baselineMeasures,
            probe.measurePasses,
        )
        assertEquals(
            "Counter-panned content size must be unchanged across cursor-anchor updates — the no-resize contract",
            baselineSize,
            probe.lastSize,
        )
    }

    @Test
    fun imeHidden_anchorChange_neverReadsAnchorOrMovesContent() {
        val anchor = mutableIntStateOf(TERMINAL_HEIGHT_PX)
        var anchorReads = 0
        setCounterPannedContent(ime = WindowInsets(bottom = 0)) {
            anchorReads++
            anchor.intValue
        }
        val baselineTop = topInRootPx(PANNED_TAG)
        assertEquals(
            "pan == 0 must short-circuit before the anchor lambda so the cursor state never enters the placement's snapshot dependency set while the keyboard is hidden",
            0,
            anchorReads,
        )

        composeRule.runOnIdle { anchor.intValue = ANCHOR_NEAR_TOP_PX }
        composeRule.waitForIdle()

        assertEquals(
            "A cursor report with the IME hidden must not re-run placement — the anchor state was never read, so it cannot invalidate anything",
            0,
            anchorReads,
        )
        assertEquals(
            "A cursor report with the IME hidden must not move the content",
            baselineTop,
            topInRootPx(PANNED_TAG),
            0f,
        )
    }

    private companion object {
        const val HOST_TAG = "counter_pan_host"
        const val PANNED_TAG = "counter_pan_content"

        /** Sized well under the Robolectric window so the height modifier is never coerced. */
        const val TERMINAL_HEIGHT_PX = 200
        const val NAV_BAR_PX = 48
        const val IME_PX = 148
        const val PAN_PX = IME_PX - NAV_BAR_PX

        /** height − anchor > pan → counter-shift saturates at the full pan. */
        const val ANCHOR_NEAR_TOP_PX = 20

        /** height − pan < anchor < height → partial counter-shift pins the anchor. */
        const val ANCHOR_MID_PX = 150
    }
}
