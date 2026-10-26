package com.mobaide.app.ui.terminal

import android.os.SystemClock
import com.termux.terminal.TerminalSession
import kotlin.math.roundToInt

/**
 * Static-mode horizontal cursor follow: while the user types, the outer
 * [TerminalHorizontalScrollView] pans so the cursor column stays on screen.
 *
 * Only keyboard-originated input arms the latch — never emulator auto-replies, mouse clicks or
 * wheel reports (a vertical drag in a mouse-tracking TUI is a stream of wheel reports, and arming
 * on it would yank the view sideways). The keystroke's echo arrives a network round trip later,
 * so every painted update inside [WINDOW_MS] of the last keystroke may reveal. Until the cursor
 * has left the burst's origin it never reveals: TUIs that park a hidden cursor at a fixed spot
 * would otherwise drag the view there on every key. Once it has moved, a return to the origin
 * (Home, Ctrl-U) is followed too. Mirrors iOS `TerminalCursorReveal`.
 *
 * Main thread only.
 */
class TerminalCursorReveal(private val clock: () -> Long = SystemClock::uptimeMillis) {

    private var deadline = 0L
    private var originCol = 0
    private var originRow = 0
    private var moved = false

    val isArmed: Boolean get() = clock() < deadline

    /** Opens (or extends) the reveal window; the origin is kept across a burst of keys. */
    fun arm(cursorCol: Int, cursorRow: Int) {
        if (!isArmed) {
            originCol = cursorCol
            originRow = cursorRow
            moved = false
        }
        deadline = clock() + WINDOW_MS
    }

    fun armAtCursor(session: TerminalSession) {
        val emulator = session.emulator ?: return
        arm(emulator.cursorCol, emulator.cursorRow)
    }

    fun disarm() {
        deadline = 0L
    }

    fun shouldReveal(cursorCol: Int, cursorRow: Int): Boolean {
        if (!isArmed) return false
        if (cursorCol != originCol || cursorRow != originRow) moved = true
        return moved
    }

    companion object {
        const val WINDOW_MS = 2_000L

        /** Cells kept visible beyond the cursor before a pan is triggered. */
        private const val EDGE_MARGIN_CELLS = 2

        /**
         * The scroll offset that brings the cursor cell into view, or null when it is already
         * visible (or there is nothing to scroll). Crossing an edge jumps a quarter viewport
         * past it, so typing pans in chunks rather than one cell per keystroke.
         */
        fun targetScrollX(
            cursorX: Float,
            cellWidth: Float,
            viewportWidth: Int,
            contentWidth: Int,
            currentScrollX: Int,
        ): Int? {
            val maxScroll = contentWidth - viewportWidth
            if (maxScroll <= 0 || cellWidth <= 0f) return null
            val margin = EDGE_MARGIN_CELLS * cellWidth
            val inset = viewportWidth / 4f
            val cursorRight = cursorX + cellWidth
            val target = when {
                cursorRight > currentScrollX + viewportWidth - margin -> cursorRight - (viewportWidth - inset)
                currentScrollX > 0 && cursorX < currentScrollX + margin -> cursorX - inset
                else -> return null
            }
            val clamped = target.roundToInt().coerceIn(0, maxScroll)
            return clamped.takeIf { it != currentScrollX }
        }
    }
}
