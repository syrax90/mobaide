package com.mobaide.app.ui.terminal

import com.mobaide.app.data.network.PaneClient
import org.json.JSONObject

/** A terminal grid in cells. */
data class TerminalGrid(val cols: Int, val rows: Int)

/**
 * The `pane` block of a Herdr pane session's state frame (PROTOCOL.md, "Herdr
 * dashboard"). In watch mode the stream is drawn for [cols] x [rows], the real
 * size of the pane; in control mode this client owns the size.
 */
data class TerminalPaneState(
    val mode: String,
    val cols: Int? = null,
    val rows: Int? = null,
    /** The client that controls the pane, in control mode. */
    val controller: String? = null,
) {
    /** This client controls the pane: it owns the size. */
    val isControl: Boolean get() = mode == MODE_CONTROL && controller == PaneClient.id

    /** Another client controls the pane; this one draws that client's grid. */
    val isControlledElsewhere: Boolean get() = mode == MODE_CONTROL && !isControl

    /** The grid the agent dictates, or null when this client owns the size. */
    val grid: TerminalGrid?
        get() {
            if (isControl || (mode != MODE_WATCH && mode != MODE_CONTROL)) return null
            if (cols == null || rows == null || cols <= 0 || rows <= 0) return null
            return TerminalGrid(cols, rows)
        }

    companion object {
        const val MODE_WATCH = "watch"
        const val MODE_CONTROL = "control"

        /** The block of a state frame body, or null for a session that is not a Herdr pane. */
        fun parse(stateJson: String): TerminalPaneState? = runCatching {
            val pane = JSONObject(stateJson).optJSONObject("pane") ?: return null
            val mode = pane.optString("mode").takeIf { it.isNotEmpty() } ?: return null
            TerminalPaneState(
                mode = mode,
                cols = pane.optInt("cols", 0).takeIf { it > 0 },
                rows = pane.optInt("rows", 0).takeIf { it > 0 },
                controller = pane.optString("controller").takeIf { it.isNotEmpty() },
            )
        }.getOrNull()
    }
}
