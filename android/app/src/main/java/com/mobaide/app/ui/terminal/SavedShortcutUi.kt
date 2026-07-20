package com.mobaide.app.ui.terminal

/**
 * A server-synced saved shortcut resolved for rendering: the chord token has
 * been parsed back into a [KeyChord] (rows with unparseable tokens — e.g.
 * saved by a newer client with keys this build doesn't know — are dropped
 * upstream) and riskiness re-derived from the local encoder.
 */
data class SavedShortcutUi(
    val id: String,
    val chord: KeyChord,
    val name: String?,
    val risky: Boolean,
) {
    /** Button/chip label: the custom name when set, else e.g. "Ctrl+X". */
    val label: String get() = name ?: chord.display()
}
