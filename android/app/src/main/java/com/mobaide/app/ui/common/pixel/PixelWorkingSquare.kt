package com.mobaide.app.ui.common.pixel

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.VectorConverter
import androidx.compose.animation.core.animateValue
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

private const val WORKING_STEP_MS = 200

// Cells as (column, row), in the clockwise order the lit cell walks.
private val WORKING_CELLS = listOf(0 to 0, 1 to 0, 1 to 1, 0 to 1)

/**
 * [PixelStatusDot]'s footprint split into four cells, one lit and walking
 * clockwise with a fading trail. Hard steps, no
 * tweening — the design system has no circular spinner.
 */
@Composable
fun PixelWorkingSquare(
    color: Color,
    modifier: Modifier = Modifier,
    size: Dp = 8.dp,
) {
    val transition = rememberInfiniteTransition(label = "px-working")
    val step by transition.animateValue(
        initialValue = 0,
        targetValue = WORKING_CELLS.size,
        typeConverter = Int.VectorConverter,
        animationSpec = infiniteRepeatable(
            tween(durationMillis = WORKING_STEP_MS * WORKING_CELLS.size, easing = LinearEasing),
        ),
        label = "px-working-step",
    )
    Canvas(modifier = modifier.size(size)) {
        val cell = this.size.width / 2
        WORKING_CELLS.forEachIndexed { index, (column, row) ->
            val alpha = when ((step - index).mod(WORKING_CELLS.size)) {
                0 -> 1f
                1 -> 0.55f
                else -> 0.25f
            }
            drawRect(
                color = color.copy(alpha = alpha),
                topLeft = Offset(column * cell, row * cell),
                size = Size(cell, cell),
            )
        }
    }
}

@Preview(name = "Working square dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun PixelWorkingSquarePreviewDark() {
    MobaideTheme(darkTheme = true) {
        Row(
            modifier = Modifier.padding(16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            PixelWorkingSquare(MaterialTheme.pixel.accent)
            PixelStatusDot(MaterialTheme.pixel.statusRunning)
        }
    }
}

@Preview(name = "Working square light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun PixelWorkingSquarePreviewLight() {
    MobaideTheme(darkTheme = false) {
        Row(
            modifier = Modifier.padding(16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            PixelWorkingSquare(MaterialTheme.pixel.accent)
            PixelStatusDot(MaterialTheme.pixel.statusRunning)
        }
    }
}
