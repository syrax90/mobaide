package com.mobaide.app.ui.common.pixel

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.imageResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import com.mobaide.app.R
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel
import kotlin.math.roundToInt

/**
 * The web `.brand-mark`: the pixel wordmark on its light plate. The bitmap is
 * a density-agnostic raster (the launcher icon is an adaptive-icon XML on
 * API 26+, which no bitmap decoder accepts), drawn at an integer device-pixel
 * multiple with nearest-neighbour sampling so the pixel art never blurs or
 * scales unevenly (web `image-rendering: pixelated`).
 */
@Composable
fun PixelBrandMark(modifier: Modifier = Modifier) {
    val bitmap = ImageBitmap.imageResource(R.drawable.brand_wordmark)
    val density = LocalDensity.current
    val scale = (density.density * 0.75f).roundToInt().coerceAtLeast(1)
    val size = with(density) {
        DpSize((bitmap.width * scale).toDp(), (bitmap.height * scale).toDp())
    }
    Image(
        bitmap = bitmap,
        contentDescription = "Mobaide",
        modifier = modifier
            .background(MaterialTheme.pixel.plate)
            .padding(horizontal = 8.dp, vertical = 5.dp)
            .size(size),
        filterQuality = FilterQuality.None,
    )
}

@Preview(name = "Brand mark dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun PixelBrandMarkPreviewDark() {
    MobaideTheme(darkTheme = true) { PixelBrandMark(Modifier.padding(16.dp)) }
}

@Preview(name = "Brand mark light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun PixelBrandMarkPreviewLight() {
    MobaideTheme(darkTheme = false) { PixelBrandMark(Modifier.padding(16.dp)) }
}
