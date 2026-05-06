package com.mobaide.app.data.local

/**
 * How the terminal surface relates to the device viewport.
 *
 * `Full` — column count auto-fits the viewport; recomputed on every layout pass (today's behavior).
 * `Static` — column count is fixed at [TerminalDisplayConfig.staticColumns] regardless of orientation;
 * the surface is wider than the viewport and the user pans horizontally.
 */
enum class TerminalDisplayMode { Full, Static }

data class TerminalDisplayConfig(
    val mode: TerminalDisplayMode,
    val staticColumns: Int,
)

const val TERMINAL_STATIC_COLUMNS_DEFAULT = 120
const val TERMINAL_STATIC_COLUMNS_MIN = 60
const val TERMINAL_STATIC_COLUMNS_MAX = 240
const val TERMINAL_STATIC_COLUMNS_STEP = 10
