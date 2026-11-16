package com.mobaide.app.ui.herdr

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.common.pixel.PixelCard
import com.mobaide.app.ui.common.pixel.PixelStatusDot
import com.mobaide.app.ui.common.pixel.PixelWorkingSquare
import com.mobaide.app.ui.sessions.SessionAttentionChip
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

const val HERDR_CARD_TAG = "herdr_card"

/**
 * The leading mark of a Herdr row: the session list's walking square while an
 * agent works, its static square otherwise. Decorative — the chip has the words.
 */
@Composable
fun HerdrStatusMark(status: HerdrStatus, modifier: Modifier = Modifier) {
    val pixel = MaterialTheme.pixel
    if (status == HerdrStatus.Working) {
        PixelWorkingSquare(color = pixel.accent, modifier = modifier)
        return
    }
    val color = when (status) {
        HerdrStatus.Blocked, HerdrStatus.Failed -> pixel.statusCrashed
        HerdrStatus.Done -> pixel.statusRunning
        HerdrStatus.None -> pixel.statusStopped
        else -> pixel.statusStarting
    }
    PixelStatusDot(color = color, modifier = modifier)
}

/**
 * The Herdr entry on the session list: one line that says how many agents run
 * in Herdr and whether one needs the user.
 */
@Composable
fun HerdrCard(
    summary: String,
    status: HerdrStatus,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    PixelCard(
        modifier = modifier
            .fillMaxWidth()
            .testTag(HERDR_CARD_TAG),
        onClick = onClick,
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            HerdrStatusMark(status)
            Spacer(modifier = Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "Herdr workspaces",
                    style = MaterialTheme.typography.titleSmall,
                    fontFamily = ChromeFontFamily,
                )
                Text(
                    text = summary,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            // Only a status that asks for the user: "Working" is in the summary.
            if (status == HerdrStatus.Blocked || status == HerdrStatus.Failed || status == HerdrStatus.Done) {
                herdrChip(status)?.let { SessionAttentionChip(it) }
            }
        }
    }
}

@Composable
private fun HerdrCardPreviewContent() {
    Column(
        modifier = Modifier.padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        HerdrCard(summary = "3 agents · 1 needs you · 1 working", status = HerdrStatus.Blocked, onClick = {})
        HerdrCard(summary = "2 agents · 2 working", status = HerdrStatus.Working, onClick = {})
        HerdrCard(summary = "No terminals", status = HerdrStatus.None, onClick = {})
    }
}

@Preview(name = "Herdr card dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun HerdrCardPreviewDark() {
    MobaideTheme(darkTheme = true) { HerdrCardPreviewContent() }
}

@Preview(name = "Herdr card light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun HerdrCardPreviewLight() {
    MobaideTheme(darkTheme = false) { HerdrCardPreviewContent() }
}
