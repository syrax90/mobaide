package com.mobaide.app.ui.terminal

import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.offset
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.layout
import androidx.compose.ui.unit.IntOffset

/**
 * Pans content up by the soft-keyboard overlap instead of resizing it.
 *
 * The lambda [offset] form is placement-only: an IME inset change re-runs
 * placement but never remeasure, so the terminal keeps its size —
 * `TerminalView.onSizeChanged` never fires, the emulator grid never reflows,
 * and no resize frame reaches the remote PTY (whose Ink-based TUIs push a
 * duplicate frame into scrollback on every SIGWINCH). A padding modifier
 * would remeasure; `graphicsLayer` would leave the interop AndroidView's
 * layout position stale (selection ActionMode rect, IME cursor anchor,
 * accessibility bounds). [systemBars] compensates for the nav-bar band the
 * surrounding Scaffold has already padded, which the IME inset overlaps.
 */
internal fun Modifier.imeFloatingPan(
    ime: WindowInsets,
    systemBars: WindowInsets,
): Modifier = offset {
    val pan = (ime.getBottom(this) - systemBars.getBottom(this)).coerceAtLeast(0)
    IntOffset(0, -pan)
}

/**
 * Counter-pans the terminal surface back down so the cursor row stays visible while
 * [imeFloatingPan] shifts the whole column up by the keyboard overlap.
 *
 * [imeFloatingPan] alone assumes the TUI's active input line sits at the grid bottom;
 * top-anchored TUIs (Antigravity/Gemini banner + input box on a fresh screen, a fresh
 * shell prompt) end up panned out of the clipped viewport with only the empty grid
 * bottom visible — and no gesture can reveal them (scrollback is at the tail, drags are
 * forwarded to mouse-tracking/alt-buffer apps). This modifier shifts the content down by
 * `clamp(height − anchorBottomPx, 0, pan)`: zero when the cursor already sits at the
 * bottom (net full pan, identical to the pre-existing behavior), the full pan when the
 * cursor is near the top (net zero pan), and in between it pins the cursor row just
 * above the InputBar. Monotonic in the IME inset, so the keyboard animation stays smooth.
 *
 * A `Modifier.layout` is used instead of the [offset] lambda because the formula needs
 * the measured height; all inset/state reads still happen inside the placement block, so
 * — like [imeFloatingPan] — changes re-run placement only, never remeasure, and the
 * emulator grid / remote PTY size stay untouched. The `pan == 0` guard keeps the
 * per-output-frame [anchorBottomPx] state out of the snapshot dependency set while the
 * keyboard is hidden, so terminal output does not re-run placement at all then.
 */
internal fun Modifier.imeCursorCounterPan(
    ime: WindowInsets,
    systemBars: WindowInsets,
    anchorBottomPx: () -> Int,
): Modifier = layout { measurable, constraints ->
    val placeable = measurable.measure(constraints)
    val density = this
    layout(placeable.width, placeable.height) {
        val pan = (ime.getBottom(density) - systemBars.getBottom(density)).coerceAtLeast(0)
        val dy = if (pan == 0) 0 else (placeable.height - anchorBottomPx()).coerceIn(0, pan)
        placeable.place(0, dy)
    }
}
