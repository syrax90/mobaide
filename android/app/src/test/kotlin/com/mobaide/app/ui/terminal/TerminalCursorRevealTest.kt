package com.mobaide.app.ui.terminal

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Static-mode typing latch and the pan math behind horizontal cursor follow. Must stay
 * in step with iOS `TerminalCursorRevealTests`: same viewport (400 px, 10 px cells → 2-cell
 * margin = 20 px, quarter-viewport inset = 100 px) and same expected offsets.
 */
class TerminalCursorRevealTest {

    private var now = 10_000L
    private val reveal = TerminalCursorReveal(clock = { now })

    // Latch

    @Test
    fun `a fresh latch never reveals`() {
        assertFalse(reveal.isArmed)
        assertFalse(reveal.shouldReveal(cursorCol = 40, cursorRow = 3))
    }

    @Test
    fun `an armed latch waits for the cursor to leave where the keystroke found it`() {
        reveal.arm(cursorCol = 5, cursorRow = 2)

        assertTrue(reveal.isArmed)
        assertFalse(
            "a TUI that parks a hidden cursor never moves it — revealing there would drag the view on every key",
            reveal.shouldReveal(cursorCol = 5, cursorRow = 2),
        )
        assertTrue("the echo moved the cursor right", reveal.shouldReveal(cursorCol = 6, cursorRow = 2))
        assertTrue("Enter moved it to another row", reveal.shouldReveal(cursorCol = 5, cursorRow = 3))
    }

    @Test
    fun `the window closes two seconds after the last keystroke`() {
        reveal.arm(cursorCol = 0, cursorRow = 0)

        now += TerminalCursorReveal.WINDOW_MS - 1
        assertTrue(reveal.shouldReveal(cursorCol = 1, cursorRow = 0))

        now += 1
        assertFalse("passive output after the window must never pan the view", reveal.isArmed)
        assertFalse(reveal.shouldReveal(cursorCol = 1, cursorRow = 0))
    }

    @Test
    fun `each keystroke extends the window but keeps the first origin`() {
        reveal.arm(cursorCol = 0, cursorRow = 0)
        now += 1_500
        reveal.arm(cursorCol = 1, cursorRow = 0)
        now += 1_500

        assertTrue("a burst of keys keeps the window open past the first deadline", reveal.isArmed)
        assertTrue(
            "the origin is the burst's first cursor, so col 1 still counts as moved",
            reveal.shouldReveal(cursorCol = 1, cursorRow = 0),
        )
        assertTrue(
            "once the burst has moved, a return to the first origin is followed too",
            reveal.shouldReveal(cursorCol = 0, cursorRow = 0),
        )
    }

    @Test
    fun `arming after the window lapsed captures a fresh origin`() {
        reveal.arm(cursorCol = 0, cursorRow = 0)
        now += TerminalCursorReveal.WINDOW_MS
        reveal.arm(cursorCol = 9, cursorRow = 4)

        assertFalse(reveal.shouldReveal(cursorCol = 9, cursorRow = 4))
        assertTrue(reveal.shouldReveal(cursorCol = 0, cursorRow = 0))
    }

    @Test
    fun `disarm closes the window at once`() {
        reveal.arm(cursorCol = 0, cursorRow = 0)

        reveal.disarm()

        assertFalse(reveal.isArmed)
        assertFalse(reveal.shouldReveal(cursorCol = 30, cursorRow = 0))
    }

    @Test
    fun `disarm resets the origin for the next burst`() {
        reveal.arm(cursorCol = 0, cursorRow = 0)
        reveal.disarm()

        reveal.arm(cursorCol = 7, cursorRow = 1)

        assertFalse(reveal.shouldReveal(cursorCol = 7, cursorRow = 1))
    }

    @Test
    fun `once the cursor has moved, a return to the origin is still followed`() {
        reveal.arm(cursorCol = 0, cursorRow = 0)
        assertTrue(reveal.shouldReveal(cursorCol = 60, cursorRow = 0))

        assertTrue(
            "Home or Ctrl-U back to the burst's origin must pan back, not strand the view at col 60",
            reveal.shouldReveal(cursorCol = 0, cursorRow = 0),
        )
    }

    @Test
    fun `a cursor parked at the origin for the whole burst never reveals`() {
        reveal.arm(cursorCol = 4, cursorRow = 1)
        now += 500
        reveal.arm(cursorCol = 4, cursorRow = 1)

        assertFalse(reveal.shouldReveal(cursorCol = 4, cursorRow = 1))
        assertFalse(reveal.shouldReveal(cursorCol = 4, cursorRow = 1))
    }

    @Test
    fun `a burst that lapsed forgets it had moved`() {
        reveal.arm(cursorCol = 0, cursorRow = 0)
        assertTrue(reveal.shouldReveal(cursorCol = 9, cursorRow = 0))
        now += TerminalCursorReveal.WINDOW_MS

        reveal.arm(cursorCol = 0, cursorRow = 0)

        assertFalse(
            "a fresh burst starts unmoved, so a parked cursor is left alone again",
            reveal.shouldReveal(cursorCol = 0, cursorRow = 0),
        )
    }

    @Test
    fun `disarm forgets the burst had moved`() {
        reveal.arm(cursorCol = 0, cursorRow = 0)
        assertTrue(reveal.shouldReveal(cursorCol = 9, cursorRow = 0))
        reveal.disarm()

        reveal.arm(cursorCol = 0, cursorRow = 0)

        assertFalse(reveal.shouldReveal(cursorCol = 0, cursorRow = 0))
    }

    // Pan math

    @Test
    fun `no overflow means nothing to pan`() {
        assertNull("Full mode: content fits the viewport", target(cursorX = 900f, contentWidth = 400))
        assertNull(target(cursorX = 900f, contentWidth = 300))
    }

    @Test
    fun `a renderer without metrics pans nowhere`() {
        assertNull(target(cursorX = 900f, cellWidth = 0f))
    }

    @Test
    fun `a visible cursor leaves the view alone`() {
        assertNull(target(cursorX = 200f))
        assertNull("scrolled right, cursor mid-viewport", target(cursorX = 700f, currentScrollX = 500))
    }

    @Test
    fun `the right margin is inclusive of two cells`() {
        // Cursor right edge 380 == 400 − 20: still inside.
        assertNull(target(cursorX = 370f))
        // One pixel more crosses the margin and jumps a quarter viewport past the edge.
        assertEquals(81, target(cursorX = 371f))
    }

    @Test
    fun `crossing the right edge parks the cursor a quarter viewport inside`() {
        // right edge 510 → offset 510 − (400 − 100) = 210; on screen at 300 of 400.
        assertEquals(210, target(cursorX = 500f))
    }

    @Test
    fun `the right jump clamps to the end of the content`() {
        // Raw target 1190 − 300 = 890, max scroll 1200 − 400 = 800.
        assertEquals(800, target(cursorX = 1180f))
    }

    @Test
    fun `an already-maxed view at the last column returns null rather than a no-op scroll`() {
        assertNull(target(cursorX = 1190f, currentScrollX = 800))
    }

    @Test
    fun `crossing the left edge parks the cursor a quarter viewport inside`() {
        // cursor 410 < 500 + 20 → offset 410 − 100 = 310.
        assertEquals(310, target(cursorX = 410f, currentScrollX = 500))
    }

    @Test
    fun `the left margin is inclusive of two cells`() {
        assertNull(target(cursorX = 520f, currentScrollX = 500))
        assertEquals(419, target(cursorX = 519f, currentScrollX = 500))
    }

    @Test
    fun `carriage return to column zero pans fully back`() {
        assertEquals(0, target(cursorX = 0f, currentScrollX = 500))
    }

    @Test
    fun `the left edge of an unscrolled view never pans`() {
        assertNull(target(cursorX = 0f, currentScrollX = 0))
    }

    private fun target(
        cursorX: Float,
        cellWidth: Float = 10f,
        viewportWidth: Int = 400,
        contentWidth: Int = 1200,
        currentScrollX: Int = 0,
    ): Int? = TerminalCursorReveal.targetScrollX(cursorX, cellWidth, viewportWidth, contentWidth, currentScrollX)
}
