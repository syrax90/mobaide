package com.mobaide.app.ui.common.pixel

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel

/** The web `.chip`: an inset plate with a 2dp ring and caption in one [tone]. */
@Composable
fun PixelChip(text: String, tone: Color, modifier: Modifier = Modifier) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceContainerHighest,
        border = pixelChipBorder(tone = tone),
        shape = RectangleShape,
        modifier = modifier,
    ) {
        Text(
            text = text.chromeCaps(),
            style = MaterialTheme.typography.labelSmall,
            color = tone,
            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
        )
    }
}

@Composable
private fun PixelChipPreviewContent() {
    Row(
        modifier = Modifier.padding(16.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        PixelChip("merging", tone = MaterialTheme.pixel.warning)
        PixelChip("worktree", tone = MaterialTheme.colorScheme.onSurfaceVariant)
        PixelChip("added", tone = MaterialTheme.pixel.ok)
        PixelChip("deleted", tone = MaterialTheme.pixel.danger)
    }
}

@Preview(name = "Chips dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun PixelChipPreviewDark() {
    MobaideTheme(darkTheme = true) { PixelChipPreviewContent() }
}

@Preview(name = "Chips light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun PixelChipPreviewLight() {
    MobaideTheme(darkTheme = false) { PixelChipPreviewContent() }
}
