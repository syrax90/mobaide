package com.mobaide.app.ui.terminal

import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.offset
import androidx.compose.ui.Modifier
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
