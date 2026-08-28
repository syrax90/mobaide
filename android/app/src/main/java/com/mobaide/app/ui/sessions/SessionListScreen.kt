package com.mobaide.app.ui.sessions

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
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.foundation.BorderStroke
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.PixelStatusDot
import com.mobaide.app.ui.theme.pixel
import com.mobaide.app.ui.common.pixel.PixelScaffold
import com.mobaide.app.ui.common.pixel.PixelSnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTopBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import com.mobaide.app.ui.theme.ChromeFontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.ui.autoscan.AutoScanCard
import com.mobaide.app.ui.common.ExternalSessionsNotice
import com.mobaide.app.ui.update.AgentOutdatedBanner

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SessionListScreen(
    viewModel: SessionListViewModel,
    onSessionClick: (String) -> Unit,
    onCreateClick: () -> Unit,
    onSettingsClick: () -> Unit,
    onProjectsClick: () -> Unit = {},
    onAutoScanClick: (String) -> Unit = {},
    onBack: (() -> Unit)? = null,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(uiState.error) {
        uiState.error?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearError()
        }
    }

    LaunchedEffect(uiState.notice) {
        uiState.notice?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearNotice()
        }
    }

    LaunchedEffect(viewModel) {
        viewModel.autoResumeSessionId.collect { sessionId ->
            onSessionClick(sessionId)
        }
    }

    PixelScaffold(
        topBar = {
            PixelTopBar(
                title = { Text("Sessions") },
                navigationIcon = {
                    if (onBack != null) {
                        IconButton(onClick = onBack) {
                            Icon(
                                Icons.AutoMirrored.Filled.ArrowBack,
                                contentDescription = "Back",
                            )
                        }
                    }
                },
                actions = {
                    IconButton(onClick = onProjectsClick) {
                        Icon(Icons.Default.Folder, contentDescription = "Projects")
                    }
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
        snackbarHost = { PixelSnackbarHost(snackbarHostState) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            uiState.outdated?.let { outdated ->
                AgentOutdatedBanner(
                    currentVersion = outdated.currentVersion,
                    latestVersion = outdated.latestVersion,
                    onInstallNow = viewModel::forceUpdateNow,
                    onDismiss = {},
                )
            }
            if (uiState.showExternalSessionsNotice) {
                ExternalSessionsNotice(onDismiss = viewModel::dismissExternalSessionsNotice)
            }
            PullToRefreshBox(
                isRefreshing = uiState.isReconnecting,
                onRefresh = viewModel::refresh,
                modifier = Modifier.fillMaxSize(),
            ) {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                if (uiState.activeScans.isNotEmpty()) {
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
                        onClick = onAutoScanClick,
                    )
                }

                if (uiState.sessions.isNotEmpty()) {
                    item {
                        Text(
                            text = "Sessions",
                            style = MaterialTheme.typography.titleSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
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
                        kindLabel = sessionKindLabel(session, uiState.customTypeNames),
                        onClick = { onSessionClick(session.session_id) },
                        onStop = { viewModel.deleteSession(session.session_id) },
                    )
                }
                }
            }
        }
    }
}

@Composable
private fun SessionCard(
    session: SessionResponse,
    kindLabel: String,
    onClick: () -> Unit,
    onStop: () -> Unit,
) {
    val stateColor = when (session.state) {
        "running" -> MaterialTheme.pixel.statusRunning
        "starting" -> MaterialTheme.pixel.statusStarting
        "stopping" -> MaterialTheme.pixel.statusStopping
        "stopped" -> MaterialTheme.pixel.statusStopped
        "crashed" -> MaterialTheme.pixel.statusCrashed
        else -> MaterialTheme.pixel.statusStopped
    }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        border = BorderStroke(2.dp, MaterialTheme.pixel.surface2),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            PixelStatusDot(color = stateColor)

            Spacer(modifier = Modifier.width(12.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = session.title
                        ?: "$kindLabel · ${session.project_name ?: session.session_id.take(8)}",
                    style = MaterialTheme.typography.titleSmall,
                    fontFamily = ChromeFontFamily,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = kindLabel,
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
