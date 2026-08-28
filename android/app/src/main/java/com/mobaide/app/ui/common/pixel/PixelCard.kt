package com.mobaide.app.ui.common.pixel

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

/**
 * The web card/plate: bg-2 fill with a 2dp surface-2 ring, flat. [selected]
 * applies the system's universal selected treatment — surface fill with a
 * brand-2 ring.
 */
@Composable
fun PixelCard(
    modifier: Modifier = Modifier,
    selected: Boolean = false,
    onClick: (() -> Unit)? = null,
    enabled: Boolean = true,
    content: @Composable ColumnScope.() -> Unit,
) {
    val fill =
        if (selected) MaterialTheme.colorScheme.surfaceContainerHighest
        else MaterialTheme.colorScheme.surfaceContainerLow
    val ring = if (selected) MaterialTheme.pixel.brand2 else MaterialTheme.pixel.surface2
    val border = BorderStroke(2.dp, ring)

    if (onClick != null) {
        Surface(
            onClick = onClick,
            modifier = modifier,
            enabled = enabled,
            shape = RectangleShape,
            color = fill,
            border = border,
        ) {
            Column(content = content)
        }
    } else {
        Surface(
            modifier = modifier,
            shape = RectangleShape,
            color = fill,
            border = border,
        ) {
            Column(content = content)
        }
    }
}

/** The web `.empty-state`: a bg-2 plate with centered gray copy. */
@Composable
fun PixelEmptyState(
    text: String,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier
            .fillMaxWidth()
            .pixelFrame(MaterialTheme.pixel.surface2),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.pixel.gray,
            modifier = Modifier.padding(32.dp),
        )
    }
}

@Preview(name = "Cards light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun PixelCardPreviewLight() {
    MobaideTheme(darkTheme = false) { PixelCardPreviewContent() }
}

@Preview(name = "Cards dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun PixelCardPreviewDark() {
    MobaideTheme(darkTheme = true) { PixelCardPreviewContent() }
}

@Composable
private fun PixelCardPreviewContent() {
    Column(
        modifier = Modifier.padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        PixelCard(modifier = Modifier.fillMaxWidth()) {
            Text("Session card", modifier = Modifier.padding(14.dp))
        }
        PixelCard(modifier = Modifier.fillMaxWidth(), selected = true, onClick = {}) {
            Text("Selected row", modifier = Modifier.padding(14.dp))
        }
        PixelEmptyState("No active sessions")
    }
}
