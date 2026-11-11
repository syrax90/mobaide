package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.layout
import androidx.compose.ui.layout.onSizeChanged
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
 * JVM tests for [imeFloatingPan] — the pan-don't-resize contract that decouples the
 * terminal grid from the soft keyboard:
 *   - No pan while the IME is hidden, and never a downward pan when the IME inset is
 *     smaller than the system-bar band the surrounding Scaffold has already padded.
 *   - When the IME shows, content pans up by exactly `ime − systemBars` pixels.
 *   - The pan is placement-only: an IME inset change re-runs placement but never
 *     remeasure, so `TerminalView.onSizeChanged` cannot fire, the emulator grid never
 *     reflows, and no `'r'` resize frame reaches the remote PTY.
 *
 * Insets are fixed-px fakes (the [WindowInsets] factory for static cases, a
 * snapshot-state-backed fake for the live-change case), so assertions are
 * density-independent. Robolectric SDK pinned to 33 to match the rest of the JVM
 * unit-test suite.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ImeFloatingPanTest {

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

    private fun setPannedContent(ime: WindowInsets, systemBars: WindowInsets) {
        composeRule.setContent {
            Box(Modifier.fillMaxSize().testTag(HOST_TAG)) {
                Box(
                    Modifier
                        .fillMaxSize()
                        .imeFloatingPan(ime, systemBars)
                        .testTag(PANNED_TAG),
                )
            }
        }
    }

    private fun topInRootPx(tag: String): Float =
        composeRule.onNodeWithTag(tag).fetchSemanticsNode().positionInRoot.y

    @Test
    fun imeShorterThanSystemBars_neverPansDownward() {
        setPannedContent(
            ime = WindowInsets(bottom = NAV_BAR_PX / 2),
            systemBars = WindowInsets(bottom = NAV_BAR_PX),
        )

        assertEquals(
            "When the IME inset is smaller than the system bars (dismiss-animation tail) the coercion must clamp the pan at zero — a negative pan would drag the terminal below the composer",
            topInRootPx(HOST_TAG),
            topInRootPx(PANNED_TAG),
            0f,
        )
    }

    @Test
    fun imeInsetChange_rerunsPlacementOnly_sizeAndMeasureCountUnchanged() {
        val ime = MutableBottomInsets(0)
        var measurePasses = 0
        var lastSize = IntSize.Zero
        composeRule.setContent {
            Box(Modifier.fillMaxSize().testTag(HOST_TAG)) {
                Box(
                    Modifier
                        .fillMaxSize()
                        .imeFloatingPan(ime, WindowInsets(bottom = NAV_BAR_PX))
                        .layout { measurable, constraints ->
                            measurePasses++
                            val placeable = measurable.measure(constraints)
                            layout(placeable.width, placeable.height) { placeable.place(0, 0) }
                        }
                        .onSizeChanged { lastSize = it }
                        .testTag(PANNED_TAG),
                )
            }
        }
        composeRule.waitForIdle()
        val baselineTop = topInRootPx(PANNED_TAG)
        val baselineMeasures = measurePasses
        val baselineSize = lastSize

        composeRule.runOnIdle { ime.bottomPx = IME_PX }
        composeRule.waitForIdle()

        assertEquals(
            "A live IME inset change (state read inside the offset lambda) must move the content by ime − systemBars",
            baselineTop - (IME_PX - NAV_BAR_PX),
            topInRootPx(PANNED_TAG),
            0f,
        )
        assertEquals(
            "IME show must re-run placement only — an extra measure pass means the terminal would reflow its grid and emit a resize frame to the remote PTY",
            baselineMeasures,
            measurePasses,
        )
        assertEquals(
            "Panned content size must be identical with the IME hidden vs shown — the no-resize contract",
            baselineSize,
            lastSize,
        )
    }

    private companion object {
        const val HOST_TAG = "ime_pan_host"
        const val PANNED_TAG = "ime_pan_content"
        const val IME_PX = 700
        const val NAV_BAR_PX = 48
    }
}
