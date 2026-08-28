package com.mobaide.app.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

// The pixel system has no border-radius. Shapes requires CornerBasedShape,
// so 0dp corners stand in for RectangleShape.
private val Square = RoundedCornerShape(0.dp)

val PixelShapes = Shapes(
    extraSmall = Square,
    small = Square,
    medium = Square,
    large = Square,
    extraLarge = Square,
)
