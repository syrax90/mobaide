package com.mobaide.app.ui.sessions

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import com.mobaide.app.ui.common.pixel.PixelChip
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

@Composable
fun SessionAttentionChip(attention: SessionAttention, modifier: Modifier = Modifier) {
    val tone = when (attention.tone) {
        AttentionTone.Warning -> MaterialTheme.pixel.warning
        AttentionTone.Ok -> MaterialTheme.pixel.ok
        AttentionTone.Muted -> MaterialTheme.pixel.textMuted
        AttentionTone.Working -> MaterialTheme.pixel.accent
    }
    PixelChip(text = attention.text, tone = tone, modifier = modifier)
}

@Preview(name = "Attention chip dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun SessionAttentionChipPreviewDark() {
    MobaideTheme(darkTheme = true) {
        SessionAttentionChip(SessionAttention("Needs permission", AttentionTone.Warning))
    }
}

@Preview(name = "Attention chip light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun SessionAttentionChipPreviewLight() {
    MobaideTheme(darkTheme = false) {
        SessionAttentionChip(SessionAttention("Ready", AttentionTone.Ok))
    }
}
