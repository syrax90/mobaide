package com.mobaide.app.ui.common

import android.content.res.Configuration
import android.os.Build
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.theme.chromeCaps
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import com.mobaide.app.ui.theme.ChromeFontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme

/**
 * Surface-variant card that shows [code] in monospace with a copy button.
 * [label] heads the card and is also the clipboard label. Set [isSensitive]
 * for token-bearing payloads so the API 33+ clipboard preview is suppressed
 * (see [copyToClipboard]). [singleLine] keeps a one-liner on one
 * horizontally-scrollable line; pass false for multi-line blocks (e.g. an
 * `.env` snippet) so they wrap instead.
 */
@Composable
fun CopyableCodeBlock(
    label: String,
    code: String,
    modifier: Modifier = Modifier,
    isSensitive: Boolean = false,
    singleLine: Boolean = true,
) {
    val context = LocalContext.current
    val haptic = LocalHapticFeedback.current
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHighest)
            .pixelFrame(MaterialTheme.colorScheme.outlineVariant)
            .padding(start = 12.dp, top = 8.dp, end = 4.dp, bottom = 8.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(
            text = label.chromeCaps(),
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SelectionContainer(modifier = Modifier.weight(1f)) {
                Text(
                    text = code,
                    style = MaterialTheme.typography.bodyMedium,
                    fontFamily = ChromeFontFamily,
                    maxLines = if (singleLine) 1 else Int.MAX_VALUE,
                    modifier = if (singleLine) {
                        Modifier.horizontalScroll(rememberScrollState())
                    } else {
                        Modifier
                    },
                )
            }
            IconButton(onClick = {
                context.copyToClipboard(label = label, text = code, isSensitive = isSensitive)
                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                // Android 13+ shows its own clipboard chip; an in-app Toast
                // would visually duplicate that confirmation.
                if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
                    Toast.makeText(context, "Copied", Toast.LENGTH_SHORT).show()
                }
            }) {
                Icon(
                    Icons.Default.ContentCopy,
                    contentDescription = "Copy $label",
                    modifier = Modifier.size(20.dp),
                )
            }
        }
    }
}

@Preview(name = "CopyableCodeBlock light", showBackground = true)
@Preview(
    name = "CopyableCodeBlock dark",
    showBackground = true,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun CopyableCodeBlockPreview() {
    MobaideTheme {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            CopyableCodeBlock(label = "Agent ID", code = "a1b2c3d4e5f6")
            CopyableCodeBlock(
                label = ".env",
                code = "RELAY_AGENT_ID=a1b2c3d4e5f6\nRELAY_AGENT_TOKEN=xVz9p-secret-token",
                isSensitive = true,
                singleLine = false,
            )
        }
    }
}
