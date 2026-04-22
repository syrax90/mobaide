package com.claudemobile.app.ui.autoscan

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Radar
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import com.claudemobile.app.ui.theme.StateCrashed
import com.claudemobile.app.ui.theme.StateRunning
import com.claudemobile.app.ui.theme.StateStarting
import com.claudemobile.app.ui.theme.StateStopped

@Composable
fun AutoScanCard(
    instance: AutoScanInstanceDto,
    onStop: (String) -> Unit,
    onClick: (String) -> Unit = {},
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onClick(instance.project_name) },
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.secondaryContainer,
        ),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    Icons.Default.Radar,
                    contentDescription = null,
                    modifier = Modifier.size(20.dp),
                    tint = statusColor(instance.status),
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = instance.project_name,
                    style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.weight(1f),
                )
                StatusChip(instance.status)
                Spacer(modifier = Modifier.width(4.dp))
                IconButton(onClick = { onStop(instance.project_name) }) {
                    Icon(
                        Icons.Default.Stop,
                        contentDescription = "Stop scan",
                        tint = MaterialTheme.colorScheme.error,
                    )
                }
            }

            Spacer(modifier = Modifier.height(4.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = "Processed: ${instance.processed_count}",
                    style = MaterialTheme.typography.bodySmall,
                )
                Spacer(modifier = Modifier.weight(1f))
                Text(
                    text = relativeTime(instance.last_activity_at),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            CountdownText(
                nextScanAtIso = instance.next_scan_at,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun StatusChip(status: String) {
    val color = statusColor(status)
    val label = when (status.lowercase()) {
        "scanning" -> "Scanning"
        "idle" -> "Idle"
        "stopped" -> "Stopped"
        "error" -> "Error"
        else -> status.replaceFirstChar { it.uppercase() }
    }
    Row(
        modifier = Modifier
            .background(color.copy(alpha = 0.2f), RoundedCornerShape(8.dp))
            .padding(horizontal = 8.dp, vertical = 2.dp),
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = color,
        )
    }
}

private fun statusColor(status: String): Color = when (status.lowercase()) {
    "scanning", "running" -> StateRunning
    "idle" -> StateStarting
    "error" -> StateCrashed
    "stopped" -> StateStopped
    else -> StateStopped
}

private fun relativeTime(epochSeconds: Double): String {
    if (epochSeconds <= 0) return ""
    val deltaSec = ((System.currentTimeMillis() / 1000.0) - epochSeconds).toLong()
    return when {
        deltaSec < 0 -> "just now" // clock skew guard
        deltaSec < 5 -> "just now"
        deltaSec < 60 -> "${deltaSec}s ago"
        deltaSec < 3600 -> "${deltaSec / 60}m ago"
        deltaSec < 86400 -> "${deltaSec / 3600}h ago"
        else -> "${deltaSec / 86400}d ago"
    }
}
