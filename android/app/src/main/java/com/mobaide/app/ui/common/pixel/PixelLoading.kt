package com.mobaide.app.ui.common.pixel

import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.keyframes
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.animateFloat
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme

/**
 * The web system's loading cue: a blinking block cursor (hard on/off at
 * 900ms, no fade). The design system has no circular spinner.
 */
@Composable
fun PixelBlinkCursor(
    modifier: Modifier = Modifier,
    height: Dp = 18.dp,
    color: Color = MaterialTheme.colorScheme.primary,
) {
    val transition = rememberInfiniteTransition(label = "px-blink")
    val alpha by transition.animateFloat(
        initialValue = 1f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            keyframes {
                durationMillis = 900
                1f at 0
                1f at 449
                0f at 450
                0f at 899
            },
        ),
        label = "px-blink-alpha",
    )
    Box(
        modifier = modifier
            .semantics {
                contentDescription = "Loading"
                progressBarRangeInfo = ProgressBarRangeInfo.Indeterminate
            }
            .width(height * 0.6f)
            .height(height)
            .graphicsLayer { this.alpha = alpha }
            .background(color),
    )
}

@Preview(name = "Blink cursor dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun PixelBlinkCursorPreviewDark() {
    MobaideTheme(darkTheme = true) {
        PixelBlinkCursor(modifier = Modifier.padding(16.dp))
    }
}

@Preview(name = "Blink cursor light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun PixelBlinkCursorPreviewLight() {
    MobaideTheme(darkTheme = false) {
        PixelBlinkCursor(modifier = Modifier.padding(16.dp))
    }
}
