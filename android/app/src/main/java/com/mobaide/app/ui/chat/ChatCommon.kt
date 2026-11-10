package com.mobaide.app.ui.chat

import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.layout
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.Constraints

/** Lays the content out at most [fraction] of the available width, pushed to the end. */
fun Modifier.alignEndFraction(fraction: Float): Modifier = layout { measurable, constraints ->
    val max = (constraints.maxWidth * fraction).toInt()
    val placeable = measurable.measure(constraints.copy(minWidth = 0, maxWidth = max))
    val width = if (constraints.hasBoundedWidth) constraints.maxWidth else placeable.width
    layout(width, placeable.height) { placeable.placeRelative(width - placeable.width, 0) }
}

/** One line of text that drops characters from its middle, keeping both ends of a path readable. */
@Composable
fun MiddleEllipsisText(
    text: String,
    style: TextStyle,
    color: Color,
    modifier: Modifier = Modifier,
) {
    val measurer = rememberTextMeasurer()
    BoxWithConstraints(modifier = modifier) {
        val maxPx = constraints.maxWidth
        val shown = remember(text, style, maxPx) {
            if (maxPx == Constraints.Infinity) return@remember text
            fun fits(candidate: String) =
                measurer.measure(candidate, style = style, maxLines = 1, softWrap = false).size.width <= maxPx
            if (fits(text)) return@remember text
            var low = 0
            var high = text.length
            while (low < high) {
                val keep = (low + high + 1) / 2
                if (fits(middleCut(text, keep))) low = keep else high = keep - 1
            }
            middleCut(text, low)
        }
        Text(text = shown, style = style, color = color, maxLines = 1, softWrap = false)
    }
}

private fun middleCut(text: String, keep: Int): String {
    if (keep >= text.length) return text
    val head = (keep + 1) / 2
    val tail = keep / 2
    return text.take(head) + "…" + text.takeLast(tail)
}
