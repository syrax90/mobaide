package com.claudemobile.app.ui.sessions

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.claudemobile.app.data.network.dto.SessionResponse
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.TextButton
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.claudemobile.app.ui.autoscan.AutoScanCard
import com.claudemobile.app.ui.autoscan.StartNewAutoScanRow
import com.claudemobile.app.ui.theme.StateCrashed
import com.claudemobile.app.ui.theme.StateRunning
import com.claudemobile.app.ui.theme.StateStarting
import com.claudemobile.app.ui.theme.StateStopped
import com.claudemobile.app.ui.theme.StateStopping

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SessionListScreen(
    viewModel: SessionListViewModel,
    onSessionClick: (String) -> Unit,
    onCreateClick: () -> Unit,
    onSettingsClick: () -> Unit,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    var showStartScanPicker by remember { mutableStateOf(false) }

    if (showStartScanPicker) {
        val activeNames = uiState.activeScans.map { it.project_name }.toSet()
        val choices = uiState.autoScanAvailableProjects.filter { it !in activeNames }
        AlertDialog(
            onDismissRequest = { showStartScanPicker = false },
            title = { Text("Start auto scan") },
            text = {
                if (choices.isEmpty()) {
                    Text("No projects available — every configured project already has an active scan.")
                } else {
                    Column {
                        choices.forEach { name ->
                            Text(
                                text = name,
                                style = MaterialTheme.typography.bodyLarge,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 12.dp)
                                    .clickable {
                                        showStartScanPicker = false
                                        viewModel.startAutoScan(name)
                                    },
                            )
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { showStartScanPicker = false }) { Text("Cancel") }
            },
        )
    }

    LaunchedEffect(uiState.error) {
        uiState.error?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearError()
        }
    }

    LaunchedEffect(viewModel) {
        viewModel.autoResumeSessionId.collect { sessionId ->
            onSessionClick(sessionId)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Sessions") },
                actions = {
                    IconButton(onClick = onSettingsClick) {
                        Icon(Icons.Default.Settings, contentDescription = "Settings")
                    }
                },
            )
        },
        floatingActionButton = {
            FloatingActionButton(onClick = onCreateClick) {
                Icon(Icons.Default.Add, contentDescription = "New Session")
            }
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        PullToRefreshBox(
            isRefreshing = uiState.isReconnecting,
            onRefresh = viewModel::refresh,
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                // Active Auto Scans (one card per running project)
                if (uiState.activeScans.isNotEmpty() || uiState.autoScanAvailableProjects.isNotEmpty()) {
                    item {
                        Text(
                            text = "Active Auto Scans",
                            style = MaterialTheme.typography.titleSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                items(uiState.activeScans, key = { it.project_name }) { instance ->
                    AutoScanCard(
                        instance = instance,
                        onStop = viewModel::stopAutoScan,
                    )
                }
                if (uiState.autoScanAvailableProjects.isNotEmpty()) {
                    item {
                        StartNewAutoScanRow(
                            enabled = uiState.activeScans.size < uiState.autoScanMax,
                            onClick = { showStartScanPicker = true },
                        )
                    }
                }

                if (uiState.sessions.isEmpty() && !uiState.isReconnecting) {
                    item {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(200.dp),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(
                                text = "No active sessions",
                                style = MaterialTheme.typography.bodyLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }

                items(uiState.sessions, key = { it.session_id }) { session ->
                    SessionCard(
                        session = session,
                        onClick = { onSessionClick(session.session_id) },
                        onStop = { viewModel.deleteSession(session.session_id) },
                    )
                }
            }
        }
    }
}

@Composable
private fun SessionCard(
    session: SessionResponse,
    onClick: () -> Unit,
    onStop: () -> Unit,
) {
    val stateColor = when (session.state) {
        "running" -> StateRunning
        "starting" -> StateStarting
        "stopping" -> StateStopping
        "stopped" -> StateStopped
        "crashed" -> StateCrashed
        else -> StateStopped
    }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // State indicator dot
            androidx.compose.foundation.Canvas(modifier = Modifier.size(12.dp)) {
                drawCircle(color = stateColor)
            }

            Spacer(modifier = Modifier.width(12.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = session.session_id,
                    style = MaterialTheme.typography.titleSmall,
                    fontFamily = FontFamily.Monospace,
                )
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = session.command.joinToString(" ").ifEmpty { "shell" },
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                session.cwd?.let { cwd ->
                    Text(
                        text = cwd,
                        style = MaterialTheme.typography.bodySmall,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = session.state.replaceFirstChar { it.uppercase() },
                    style = MaterialTheme.typography.labelSmall,
                    color = stateColor,
                )
            }

            if (session.state == "running" || session.state == "starting") {
                IconButton(onClick = onStop) {
                    Icon(
                        Icons.Default.Stop,
                        contentDescription = "Stop session",
                        tint = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }
    }
}
