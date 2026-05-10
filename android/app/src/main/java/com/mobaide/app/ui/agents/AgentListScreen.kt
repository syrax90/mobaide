package com.mobaide.app.ui.agents

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.TextButton
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.ui.theme.StateRunning
import com.mobaide.app.ui.theme.StateStopped

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AgentListScreen(
    viewModel: AgentListViewModel,
    onAgentConnected: () -> Unit,
    onSettingsClick: () -> Unit,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    val qrScanLauncher = rememberQrPairingScanner()

    LaunchedEffect(qrScanLauncher) {
        qrScanLauncher.warmUp()
    }

    LaunchedEffect(qrScanLauncher) {
        viewModel.scanRequests.collect {
            qrScanLauncher.launch { result -> viewModel.onScanResult(result) }
        }
    }

    LaunchedEffect(Unit) {
        viewModel.navigateToSessions.collect {
            onAgentConnected()
        }
    }

    LaunchedEffect(uiState.error) {
        uiState.error?.let { error ->
            snackbarHostState.showSnackbar(error)
            viewModel.clearError()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("My Agents") },
                actions = {
                    AgentListOverflowMenu(onAddManually = viewModel::showAddDialog)
                    IconButton(onClick = onSettingsClick) {
                        Icon(Icons.Default.Settings, contentDescription = "Settings")
                    }
                },
            )
        },
        floatingActionButton = {
            ScanQrFab(onClick = viewModel::onScanQrClicked)
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            if (uiState.isScannerLaunching) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            }
            PullToRefreshBox(
                isRefreshing = uiState.isLoading,
                onRefresh = viewModel::refresh,
                modifier = Modifier.fillMaxSize(),
            ) {
                if (uiState.agents.isEmpty() && !uiState.isLoading) {
                    EmptyAgentsState(
                        onScanClick = viewModel::onScanQrClicked,
                        onAddManuallyClick = viewModel::showAddDialog,
                    )
                } else {
                    LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(
                            horizontal = 16.dp,
                            vertical = 8.dp,
                        ),
                    ) {
                        items(uiState.agents, key = { it.agent_id }) { agent ->
                            AgentCard(
                                agent = agent,
                                isConnected = agent.agent_id == uiState.connectedAgentId,
                                isConnecting = uiState.isConnecting,
                                onClick = { viewModel.connectToAgent(agent) },
                                onReRegister = { viewModel.showReRegisterDialog(agent) },
                                onDelete = { viewModel.showDeleteConfirm(agent) },
                            )
                        }
                    }
                }
            }
        }

        if (uiState.isConnecting) {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center,
            ) {
                CircularProgressIndicator()
            }
        }
    }

    AddAgentDialog(
        state = uiState.addDialogState,
        onCreateAgent = viewModel::createAgent,
        onDismiss = viewModel::dismissAddDialog,
    )

    ReRegisterDialog(
        state = uiState.reRegisterDialogState,
        onConfirm = viewModel::confirmReRegister,
        onDismiss = viewModel::dismissReRegisterDialog,
    )

    uiState.deleteConfirmAgent?.let { agent ->
        DeleteAgentDialog(
            agentName = agent.name,
            onConfirm = { viewModel.confirmDelete(agent.agent_id) },
            onDismiss = viewModel::dismissDeleteConfirm,
        )
    }

    QrPairingDialogs(
        state = uiState.qrPairing,
        onConfirm = viewModel::confirmQrClaim,
        onDismiss = viewModel::dismissQrPairing,
    )
}

/** `internal` (vs the screen file's usual `private`) so `AgentListScreenTest` can compose it directly. */
@Composable
internal fun ScanQrFab(onClick: () -> Unit) {
    ExtendedFloatingActionButton(
        onClick = onClick,
        icon = { Icon(Icons.Default.QrCodeScanner, contentDescription = null) },
        text = { Text("Scan to add") },
    )
}

/** `internal` (vs the screen file's usual `private`) so `AgentListScreenTest` can compose it directly. */
@Composable
internal fun AgentListOverflowMenu(onAddManually: () -> Unit) {
    var menuExpanded by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { menuExpanded = true }) {
            Icon(Icons.Default.MoreVert, contentDescription = "More options")
        }
        DropdownMenu(
            expanded = menuExpanded,
            onDismissRequest = { menuExpanded = false },
        ) {
            DropdownMenuItem(
                text = { Text("Add manually...") },
                onClick = {
                    menuExpanded = false
                    onAddManually()
                },
                leadingIcon = {
                    Icon(Icons.Default.Add, contentDescription = null)
                },
            )
        }
    }
}

/** `internal` (vs the screen file's usual `private`) so `AgentListScreenTest` can compose it directly. */
@Composable
internal fun EmptyAgentsState(
    onScanClick: () -> Unit,
    onAddManuallyClick: () -> Unit,
) {
    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
            modifier = Modifier.padding(32.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(96.dp)
                    .background(
                        color = MaterialTheme.colorScheme.secondaryContainer,
                        shape = CircleShape,
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    Icons.Default.QrCodeScanner,
                    contentDescription = null,
                    modifier = Modifier.size(56.dp),
                    tint = MaterialTheme.colorScheme.onSecondaryContainer,
                )
            }
            Text(
                text = "No agents yet",
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.onSurface,
            )
            Text(
                text = "Scan the QR code shown by `mobaide-agent show-qr` on your host to add an agent.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
            Button(
                onClick = onScanClick,
                contentPadding = ButtonDefaults.ButtonWithIconContentPadding,
            ) {
                Icon(
                    Icons.Default.QrCodeScanner,
                    contentDescription = null,
                    modifier = Modifier.size(ButtonDefaults.IconSize),
                )
                Spacer(modifier = Modifier.width(ButtonDefaults.IconSpacing))
                Text("Scan QR code")
            }
            TextButton(onClick = onAddManuallyClick) {
                Text("Add manually instead")
            }
        }
    }
}

@Composable
private fun QrPairingDialogs(
    state: QrPairingState,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    when (state) {
        QrPairingState.Idle -> Unit
        is QrPairingState.Confirming -> AlertDialog(
            onDismissRequest = onDismiss,
            title = { Text("Pair this agent?") },
            text = {
                Text(
                    "Relay: ${state.payload.relayUrl}\n" +
                    "Pairing code: ${state.payload.nonce.take(8)}…\n" +
                    "Expires at unix ${state.payload.expUnix}.",
                )
            },
            confirmButton = {
                TextButton(onClick = onConfirm) { Text("Pair") }
            },
            dismissButton = {
                TextButton(onClick = onDismiss) { Text("Cancel") }
            },
        )
        is QrPairingState.Claiming -> AlertDialog(
            onDismissRequest = {},
            title = { Text("Pairing…") },
            text = {
                Box(
                    modifier = Modifier.fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator()
                }
            },
            confirmButton = {},
        )
        is QrPairingState.Success -> AlertDialog(
            onDismissRequest = onDismiss,
            title = { Text("Paired") },
            text = {
                Text(
                    "Agent ${state.friendlyName} (${state.agentId}) " +
                    "has been paired to your account.",
                )
            },
            confirmButton = {
                TextButton(onClick = onDismiss) { Text("OK") }
            },
        )
        is QrPairingState.Error -> AlertDialog(
            onDismissRequest = onDismiss,
            title = { Text("Pairing failed") },
            text = { Text(state.message) },
            confirmButton = {
                OutlinedButton(onClick = onDismiss) { Text("Close") }
            },
        )
    }
}

@Composable
private fun AgentCard(
    agent: AgentSummaryResponse,
    isConnected: Boolean,
    isConnecting: Boolean,
    onClick: () -> Unit,
    onReRegister: () -> Unit,
    onDelete: () -> Unit,
) {
    var menuExpanded by remember { mutableStateOf(false) }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = !isConnecting) { onClick() },
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val statusColor = if (agent.online) StateRunning else StateStopped
            Canvas(modifier = Modifier.size(12.dp)) {
                drawCircle(color = statusColor)
            }

            Spacer(modifier = Modifier.width(12.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = agent.name,
                    style = MaterialTheme.typography.titleMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = agent.agent_id,
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = if (agent.online) "Online" else "Offline",
                    style = MaterialTheme.typography.labelSmall,
                    color = if (agent.online) StateRunning else StateStopped,
                )
            }

            if (isConnected) {
                Icon(
                    Icons.Default.CheckCircle,
                    contentDescription = "Connected",
                    tint = StateRunning,
                    modifier = Modifier.size(24.dp),
                )
                Spacer(modifier = Modifier.width(4.dp))
            }

            Box {
                IconButton(onClick = { menuExpanded = true }) {
                    Icon(Icons.Default.MoreVert, contentDescription = "Options")
                }
                DropdownMenu(
                    expanded = menuExpanded,
                    onDismissRequest = { menuExpanded = false },
                ) {
                    DropdownMenuItem(
                        text = { Text("Re-register") },
                        onClick = {
                            menuExpanded = false
                            onReRegister()
                        },
                        leadingIcon = {
                            Icon(Icons.Default.Refresh, contentDescription = null)
                        },
                    )
                    DropdownMenuItem(
                        text = {
                            Text(
                                "Delete",
                                color = MaterialTheme.colorScheme.error,
                            )
                        },
                        onClick = {
                            menuExpanded = false
                            onDelete()
                        },
                        leadingIcon = {
                            Icon(
                                Icons.Default.Delete,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.error,
                            )
                        },
                    )
                }
            }
        }
    }
}
