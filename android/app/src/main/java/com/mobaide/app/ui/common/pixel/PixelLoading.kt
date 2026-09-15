package com.mobaide.app.ui.common.pixel

import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.keyframes
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.animateFloat
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
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

/**
 * A two-rectangle determinate bar; `null` is the indeterminate state, drawn as
 * the blink cursor. No width animation: git's per-phase percentage restarts at
 * every phase and an animated fill would run backwards each time.
 */
@Composable
fun PixelProgressBar(
    progress: Float?,
    modifier: Modifier = Modifier,
    height: Dp = 6.dp,
) {
    if (progress == null) {
        PixelBlinkCursor(modifier = modifier, height = 12.dp)
        return
    }
    val clamped = progress.coerceIn(0f, 1f)
    Box(
        modifier = modifier
            .semantics { progressBarRangeInfo = ProgressBarRangeInfo(clamped, 0f..1f) }
            .fillMaxWidth()
            .height(height)
            .background(MaterialTheme.colorScheme.surfaceContainerHigh),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth(clamped)
                .fillMaxHeight()
                .background(MaterialTheme.colorScheme.primary),
        )
    }
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
