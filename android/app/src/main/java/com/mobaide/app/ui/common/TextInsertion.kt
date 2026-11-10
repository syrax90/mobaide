package com.mobaide.app.ui.common

import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue

/**
 * [inserted] at this value's cursor, replacing a selection, cursor after it.
 * With [spaced], a space goes on either side where the text would run into a
 * word (dictated text); without it the text goes in as is (a template body).
 */
fun TextFieldValue.insertAtCursor(inserted: String, spaced: Boolean = false): TextFieldValue {
    val start = selection.min.coerceIn(0, text.length)
    val end = selection.max.coerceIn(start, text.length)
    val before = text.substring(0, start)
    val after = text.substring(end)
    val lead = if (spaced && before.isNotEmpty() && !before.last().isWhitespace()) " " else ""
    val trail = if (spaced && after.isNotEmpty() && !after.first().isWhitespace()) " " else ""
    val piece = lead + inserted + trail
    return TextFieldValue(
        text = before + piece + after,
        selection = TextRange(before.length + piece.length),
    )
}
