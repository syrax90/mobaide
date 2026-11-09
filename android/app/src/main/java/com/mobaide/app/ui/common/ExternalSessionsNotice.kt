package com.mobaide.app.ui.common

import android.content.res.Configuration
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.theme.pixel
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme

const val EXTERNAL_SESSIONS_NOTICE_TAG = "external_sessions_notice"
const val EXTERNAL_SESSIONS_NOTICE_DISMISS_TAG = "external_sessions_notice_dismiss"

/**
 * Informational rather than alarming — the container is
 * [androidx.compose.material3.ColorScheme.surfaceVariant], not an error tone,
 * because nothing is broken. Dismissal is permanent, which is why the
 * affordance is a labelled button rather than a bare close glyph.
 */
@Composable
fun ExternalSessionsNotice(
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp)
            .background(MaterialTheme.colorScheme.surfaceContainerHighest)
            .pixelFrame(MaterialTheme.pixel.rule)
            .padding(12.dp)
            .testTag(EXTERNAL_SESSIONS_NOTICE_TAG),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                imageVector = Icons.Outlined.Info,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(20.dp),
            )
            Spacer(modifier = Modifier.size(8.dp))
            Text(
                text = "Sessions started outside Mobaide won't appear here",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = "Mobaide lists only sessions its own agent started. To continue a Claude Code, " +
                "Codex or Antigravity session from your PC, start a new session of that agent " +
                "and choose Continue a session.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Row(
            horizontalArrangement = Arrangement.End,
            modifier = Modifier.fillMaxWidth(),
        ) {
            PixelTextButton(
                onClick = onDismiss,
                modifier = Modifier.testTag(EXTERNAL_SESSIONS_NOTICE_DISMISS_TAG),
            ) {
                Text("Got it")
            }
        }
    }
}

@Preview(name = "ExternalSessionsNotice light", showBackground = true)
@Preview(
    name = "ExternalSessionsNotice dark",
    showBackground = true,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun ExternalSessionsNoticePreview() {
    MobaideTheme {
        ExternalSessionsNotice(onDismiss = {})
    }
}
