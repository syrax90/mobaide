package com.mobaide.app.ui.common.pixel

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.material3.MaterialTheme

/** The web status dot: an 8dp square, never a circle. */
@Composable
fun PixelStatusDot(
    color: Color,
    modifier: Modifier = Modifier,
    size: Dp = 8.dp,
) {
    Box(modifier = modifier.size(size).background(color))
}

@Preview(name = "Status dots dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun PixelStatusDotPreviewDark() {
    MobaideTheme(darkTheme = true) {
        Row(
            modifier = Modifier.padding(16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            PixelStatusDot(MaterialTheme.pixel.statusRunning)
            PixelStatusDot(MaterialTheme.pixel.statusStarting)
            PixelStatusDot(MaterialTheme.pixel.statusCrashed)
            PixelStatusDot(MaterialTheme.pixel.statusStopped)
        }
    }
}

@Preview(name = "Status dots light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun PixelStatusDotPreviewLight() {
    MobaideTheme(darkTheme = false) {
        Row(
            modifier = Modifier.padding(16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            PixelStatusDot(MaterialTheme.pixel.statusRunning)
            PixelStatusDot(MaterialTheme.pixel.statusStarting)
            PixelStatusDot(MaterialTheme.pixel.statusCrashed)
            PixelStatusDot(MaterialTheme.pixel.statusStopped)
        }
    }
}
