package com.mobaide.app.ui.common.pixel

import androidx.compose.foundation.border
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ClipOp
import androidx.compose.ui.graphics.PointMode
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.layout.layout
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp

/**
 * The pixel system's only border: a 2dp ring drawn inside the bounds
 * (the web's `box-shadow: inset 0 0 0 2px`). Never a 1dp hairline.
 */
fun Modifier.pixelFrame(color: Color, width: Dp = 2.dp): Modifier =
    border(width, color, RectangleShape)

/**
 * The pixel system's only elevation: a solid rect offset (+4dp, +4dp) with no
 * blur. Draws outside the bounds — parents must not clip, and siblings need
 * >= 4dp of trailing/bottom room.
 */
fun Modifier.hardShadow(color: Color, offset: Dp = 4.dp, visible: Boolean = true): Modifier =
    drawBehind {
        if (visible) {
            val px = offset.toPx()
            // Like CSS box-shadow, never paint beneath the element itself —
            // a transparent (ghost) fill must not reveal the shadow.
            clipRect(0f, 0f, size.width, size.height, ClipOp.Difference) {
                drawRect(color = color, topLeft = Offset(px, px), size = size)
            }
        }
    }

/**
 * Press feedback: the content travels (+4dp, +4dp) into the spot the
 * [hardShadow] vacates. Deliberately unanimated — the web transition is
 * stepped, not eased.
 */
fun Modifier.pressTravel(pressed: Boolean, travel: Dp = 4.dp): Modifier = layout { measurable, constraints ->
    val placeable = measurable.measure(constraints)
    layout(placeable.width, placeable.height) {
        val shift = if (pressed) travel.roundToPx() else 0
        placeable.place(IntOffset(shift, shift))
    }
}

/**
 * The page texture behind every screen: a faint dot lattice
 * (the web's `radial-gradient(var(--surface-2) 1px, transparent 1px)` on a
 * 24px grid).
 */
fun Modifier.dotGrid(dotColor: Color, spacing: Dp = 24.dp, dotRadius: Dp = 1.dp): Modifier =
    drawWithCache {
        val step = spacing.toPx()
        val bounds = size
        val points = buildList {
            var y = step / 2f
            while (y < bounds.height) {
                var x = step / 2f
                while (x < bounds.width) {
                    add(Offset(x, y))
                    x += step
                }
                y += step
            }
        }
        onDrawBehind {
            drawPoints(
                points = points,
                pointMode = PointMode.Points,
                color = dotColor,
                strokeWidth = dotRadius.toPx() * 2f,
                cap = StrokeCap.Round,
            )
        }
    }
