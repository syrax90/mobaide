package com.claudemobile.app.ui.autoscan

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Radar
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.claudemobile.app.data.network.dto.ScanStatusResponse
import com.claudemobile.app.ui.theme.StateRunning
import com.claudemobile.app.ui.theme.StateStopped

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AutoScanCard(
    status: ScanStatusResponse,
    onStart: (String) -> Unit,
    onStop: () -> Unit,
) {
    val isRunning = status.status == "running" || status.status == "scanning"
    var expanded by remember { mutableStateOf(false) }
    var selectedProject by remember { mutableStateOf(status.active_project ?: "") }

    Card(
        modifier = Modifier.fillMaxWidth(),
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
                    tint = if (isRunning) StateRunning else StateStopped,
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = "Auto Scan",
                    style = MaterialTheme.typography.titleSmall,
                )
                Spacer(modifier = Modifier.weight(1f))
                Text(
                    text = status.status.replaceFirstChar { it.uppercase() },
                    style = MaterialTheme.typography.labelMedium,
                    color = if (isRunning) StateRunning else StateStopped,
                )
            }

            Spacer(modifier = Modifier.height(8.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = "Processed: ${status.processed_count}",
                    style = MaterialTheme.typography.bodySmall,
                )
                Spacer(modifier = Modifier.weight(1f))
                status.active_project?.let {
                    Text(
                        text = "Project: $it",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }

            if (!isRunning && status.available_projects.isNotEmpty()) {
                Spacer(modifier = Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    ExposedDropdownMenuBox(
                        expanded = expanded,
                        onExpandedChange = { expanded = it },
                        modifier = Modifier.weight(1f),
                    ) {
                        OutlinedTextField(
                            value = selectedProject,
                            onValueChange = {},
                            readOnly = true,
                            label = { Text("Project") },
                            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
                            modifier = Modifier.menuAnchor(MenuAnchorType.PrimaryNotEditable),
                        )
                        ExposedDropdownMenu(
                            expanded = expanded,
                            onDismissRequest = { expanded = false },
                        ) {
                            status.available_projects.forEach { project ->
                                DropdownMenuItem(
                                    text = { Text(project) },
                                    onClick = {
                                        selectedProject = project
                                        expanded = false
                                    },
                                )
                            }
                        }
                    }
                    Spacer(modifier = Modifier.width(8.dp))
                    IconButton(
                        onClick = { if (selectedProject.isNotBlank()) onStart(selectedProject) },
                    ) {
                        Icon(Icons.Default.PlayArrow, contentDescription = "Start scan")
                    }
                }
            }

            if (isRunning) {
                Spacer(modifier = Modifier.height(8.dp))
                IconButton(onClick = onStop) {
                    Icon(
                        Icons.Default.Stop,
                        contentDescription = "Stop scan",
                        tint = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }
    }
}
