package com.mobaide.app.ui.terminal

import com.mobaide.app.data.local.TerminalDisplayMode
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * JVM unit tests for [TerminalGridEstimate], the grid the create request names
 * before any terminal exists to measure.
 *
 * The agent spawns the PTY at that grid and the program lays its opening frames
 * out there. Those bytes then sit in the agent's replay ring carrying no width
 * of their own, so a wrong column count is not self-correcting — every later
 * reconnect re-renders them at the real grid.
 *
 * Robolectric because [TerminalGridEstimate.of] measures a [android.graphics.Paint];
 * SDK pinned to 33 to match the rest of the JVM unit-test suite.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalGridEstimateTest {

    private fun viewport(width: Int, height: Int): TerminalViewport =
        TerminalViewport(widthPx = width, heightPx = height)

    @Test
    fun columns_staticMode_ignoresTheViewportEntirely() {
        // Static draws a surface wider than the screen and pans it, so the count
        // is exact and owes nothing to geometry. A viewport that would divide
        // down to 10 columns must still report the configured 120.
        val columns = TerminalGridEstimate.columns(
            mode = TerminalDisplayMode.Static,
            staticColumns = 120,
            viewportWidthPx = 200,
            fontWidth = 20f,
        )

        assertEquals(120, columns)
    }

    @Test
    fun columns_fullMode_dividesTheViewportByTheCell() {
        val columns = TerminalGridEstimate.columns(
            mode = TerminalDisplayMode.Full,
            staticColumns = 120,
            viewportWidthPx = 1080,
            fontWidth = 20f,
        )

        assertEquals(54, columns)
    }

    @Test
    fun columns_fullMode_fallsBackToStaticColumns_whenNothingCanBeMeasured() {
        // An unmeasurable font or a not-yet-laid-out display must not produce a
        // divide-by-zero grid; the configured Static count is the sane stand-in.
        assertEquals(
            120,
            TerminalGridEstimate.columns(
                mode = TerminalDisplayMode.Full,
                staticColumns = 120,
                viewportWidthPx = 1080,
                fontWidth = 0f,
            ),
        )
        assertEquals(
            120,
            TerminalGridEstimate.columns(
                mode = TerminalDisplayMode.Full,
                staticColumns = 120,
                viewportWidthPx = 0,
                fontWidth = 20f,
            ),
        )
    }

    @Test
    fun columns_fullMode_floorsAtTheEmulatorMinimum() {
        // A viewport narrower than a single cell still divides to zero, and a
        // zero-column PTY is not a grid any program can lay out in.
        val columns = TerminalGridEstimate.columns(
            mode = TerminalDisplayMode.Full,
            staticColumns = 120,
            viewportWidthPx = 10,
            fontWidth = 20f,
        )

        assertEquals(4, columns)
    }

    @Test
    fun rows_dividesTheViewportByTheLineSpacing() {
        assertEquals(60, TerminalGridEstimate.rows(viewportHeightPx = 2400, lineSpacing = 40))
    }

    @Test
    fun rows_fallsBackToTheDefault_whenNothingCanBeMeasured() {
        assertEquals(24, TerminalGridEstimate.rows(viewportHeightPx = 2400, lineSpacing = 0))
        assertEquals(24, TerminalGridEstimate.rows(viewportHeightPx = 0, lineSpacing = 40))
    }

    @Test
    fun rows_floorsAtTheEmulatorMinimum() {
        assertEquals(4, TerminalGridEstimate.rows(viewportHeightPx = 10, lineSpacing = 40))
    }

    @Test
    fun of_labelsTheAxesRatherThanOrderingThem() {
        // A zero-extent display pins the row axis to its documented fallback
        // and Static pins the column axis exactly, so each axis is identified
        // without depending on the host's font stack — which Robolectric does
        // not reproduce. `TerminalGrid` names both, so a swap at the call site
        // no longer compiles; this pins the fallbacks that identify them.
        val grid = TerminalGridEstimate.of(
            mode = TerminalDisplayMode.Static,
            staticColumns = 120,
            viewport = viewport(width = 0, height = 0),
        )

        assertEquals(TerminalGrid(rows = 24, cols = 120), grid)
    }

    @Test
    fun of_keepsTheStaticColumnCountOffTheDisplayWidth() {
        // Static is the shipped default, so this is the path almost every
        // session is created on: whatever the display reports, the column axis
        // is the preference and nothing else.
        val wide = TerminalGridEstimate.of(
            mode = TerminalDisplayMode.Static,
            staticColumns = 120,
            viewport = viewport(width = 1080, height = 2400),
        )
        val narrow = TerminalGridEstimate.of(
            mode = TerminalDisplayMode.Static,
            staticColumns = 120,
            viewport = viewport(width = 320, height = 2400),
        )

        assertEquals(120, wide.cols)
        assertEquals(120, narrow.cols)
        assertEquals("display width must not reach the row axis either", wide.rows, narrow.rows)
    }
}
