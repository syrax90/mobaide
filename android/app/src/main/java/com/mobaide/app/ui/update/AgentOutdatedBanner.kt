package com.mobaide.app.ui.update

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/**
 * Non-blocking banner that surfaces an agent-version mismatch detected via
 * [com.mobaide.app.data.events.AgentEvent.AgentOutdated]. The user can
 * dismiss to suppress for the current session; the staged-binary auto-updater
 * still applies the update silently when the agent is idle.
 */
@Composable
fun AgentOutdatedBanner(
    currentVersion: String,
    latestVersion: String,
    onInstallNow: () -> Unit,
    onDismiss: () -> Unit,
) {
    var dismissed by remember { mutableStateOf(false) }
    if (dismissed) return
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.tertiaryContainer)
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(
            text = "Agent v$currentVersion → v$latestVersion. Will install when idle.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onTertiaryContainer,
            modifier = Modifier.weight(1f),
        )
        PixelTextButton(onClick = onInstallNow) { Text("Install now") }
        PixelTextButton(onClick = {
            dismissed = true
            onDismiss()
        }) { Text("Dismiss") }
    }
}
