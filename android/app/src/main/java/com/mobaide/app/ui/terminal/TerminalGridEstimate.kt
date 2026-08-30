package com.mobaide.app.ui.terminal

import android.graphics.Paint
import android.graphics.Typeface
import android.util.DisplayMetrics
import com.mobaide.app.data.local.TerminalDisplayMode
import kotlin.math.ceil
import kotlin.math.max

/**
 * The grid a terminal will have, computed before one exists.
 *
 * A session's PTY is spawned by the create request, long before its terminal is
 * laid out, so the create screen has to name the grid up front. Getting it wrong
 * is not self-correcting: the program lays its opening frames out at the spawn
 * width, those bytes enter the agent's replay ring, and every later reconnect
 * re-renders them at the real grid.
 *
 * Columns are the axis worth getting right — in [TerminalDisplayMode.Static]
 * they are exact and need no geometry. Rows are an estimate either way and the
 * first real layout corrects them.
 */
/** A terminal grid. Named rather than a `Pair` — both axes are `Int`, and a
 * swapped pair reaches the wire as a plausible-looking PTY size. */
data class TerminalGrid(val rows: Int, val cols: Int)

object TerminalGridEstimate {
    /**
     * Reproduces [com.termux.view.TerminalRenderer]'s metrics, whose inputs are
     * the constants `TerminalSurfaceView` installs on the real view.
     */
    fun cellSize(textSizePx: Float = TERMINAL_TEXT_SIZE_PX): Pair<Float, Int> {
        val paint = Paint().apply {
            typeface = Typeface.MONOSPACE
            textSize = textSizePx
        }
        val fontWidth = paint.measureText("X")
        // Not clamped to 1: a zero here means the font could not be measured, and
        // `rows` must fall back rather than divide the viewport by one pixel and
        // propose thousands of rows.
        val lineSpacing = ceil(paint.fontSpacing.toDouble()).toInt()
        return fontWidth to lineSpacing
    }

    fun columns(
        mode: TerminalDisplayMode,
        staticColumns: Int,
        viewportWidthPx: Int,
        fontWidth: Float,
    ): Int = when (mode) {
        TerminalDisplayMode.Static -> staticColumns
        TerminalDisplayMode.Full ->
            if (fontWidth > 0f && viewportWidthPx > 0) {
                max(MIN_DIMENSION, (viewportWidthPx / fontWidth).toInt())
            } else {
                staticColumns
            }
    }

    fun rows(viewportHeightPx: Int, lineSpacing: Int): Int =
        if (lineSpacing > 0 && viewportHeightPx > 0) {
            max(MIN_DIMENSION, viewportHeightPx / lineSpacing)
        } else {
            DEFAULT_ROWS
        }

    fun of(
        mode: TerminalDisplayMode,
        staticColumns: Int,
        metrics: DisplayMetrics,
    ): TerminalGrid {
        val (fontWidth, lineSpacing) = cellSize()
        return TerminalGrid(
            rows = rows(metrics.heightPixels, lineSpacing),
            cols = columns(mode, staticColumns, metrics.widthPixels, fontWidth),
        )
    }

    /** Matches `TerminalSurfaceView`'s `setTextSize(36)` on the real view. */
    const val TERMINAL_TEXT_SIZE_PX = 36f

    /** `TerminalView.updateSize` floors both axes at 4. */
    private const val MIN_DIMENSION = 4

    private const val DEFAULT_ROWS = 24
}
