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
import androidx.compose.material.icons.filled.Keyboard
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Terminal
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelIconButton
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.material3.FloatingActionButtonDefaults
import com.mobaide.app.ui.common.pixel.PixelStatusDot
import com.mobaide.app.ui.common.pixel.hardShadow
import com.mobaide.app.ui.common.pixel.pressTravel
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.theme.pixel
import com.mobaide.app.ui.common.pixel.PixelScaffold
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.PixelSnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTopBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import com.mobaide.app.ui.theme.ChromeFontFamily
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.ui.common.CopyableCodeBlock
import com.mobaide.app.ui.common.GraceBannerSection

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

    PixelScaffold(
        topBar = {
            PixelTopBar(
                title = { Text("My Agents") },
                actions = {
                    AgentListOverflowMenu(
                        onShowInstall = viewModel::showInstallSheet,
                        onAddManually = viewModel::showAddDialog,
                        onPairWithCode = viewModel::startDeviceCodePairing,
                    )
                    PixelIconButton(Icons.Default.Settings, "Settings", onClick = onSettingsClick)
                },
            )
        },
        floatingActionButton = {
            ScanQrFab(onClick = viewModel::onScanQrClicked)
        },
        snackbarHost = { PixelSnackbarHost(snackbarHostState) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            GraceBannerSection()
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
                        onShowInstallClick = viewModel::showInstallSheet,
                        onScanClick = viewModel::onScanQrClicked,
                        onPairWithCodeClick = viewModel::startDeviceCodePairing,
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
                PixelBlinkCursor()
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

    DeviceCodePairingDialogs(
        state = uiState.deviceCodePairing,
        onConfirm = viewModel::confirmDeviceCode,
        onDismiss = viewModel::dismissDeviceCodePairing,
    )

    if (uiState.showInstallSheet) {
        InstallCommandsSheet(onDismiss = viewModel::dismissInstallSheet)
    }
}

/** `internal` (vs the screen file's usual `private`) so `AgentListScreenTest` can compose it directly. */
@Composable
internal fun ScanQrFab(onClick: () -> Unit) {
    val source = remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    ExtendedFloatingActionButton(
        onClick = onClick,
        icon = { Icon(Icons.Default.QrCodeScanner, contentDescription = null) },
        text = { Text("Scan to add") },
        modifier = Modifier
            .hardShadow(MaterialTheme.pixel.btnShadow, visible = !pressed)
            .pressTravel(pressed),
        elevation = FloatingActionButtonDefaults.elevation(0.dp, 0.dp, 0.dp, 0.dp),
        interactionSource = source,
    )
}

/** `internal` (vs the screen file's usual `private`) so `AgentListScreenTest` can compose it directly. */
@Composable
internal fun AgentListOverflowMenu(
    onShowInstall: () -> Unit,
    onAddManually: () -> Unit,
    onPairWithCode: () -> Unit,
) {
    var menuExpanded by remember { mutableStateOf(false) }
    Box {
        PixelIconButton(Icons.Default.MoreVert, "More options", onClick = { menuExpanded = true })
        DropdownMenu(
            expanded = menuExpanded,
            onDismissRequest = { menuExpanded = false },
        ) {
            // Above "show install commands" because it is the path that works
            // when the agent host's screen is not somewhere a camera can see —
            // an SSH session, a VM console, a machine in another room.
            DropdownMenuItem(
                text = { Text("Pair with a code") },
                onClick = {
                    menuExpanded = false
                    onPairWithCode()
                },
                leadingIcon = {
                    Icon(Icons.Default.Keyboard, contentDescription = null)
                },
            )
            DropdownMenuItem(
                text = { Text("Show install commands") },
                onClick = {
                    menuExpanded = false
                    onShowInstall()
                },
                leadingIcon = {
                    Icon(Icons.Default.Terminal, contentDescription = null)
                },
            )
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
    onShowInstallClick: () -> Unit,
    onScanClick: () -> Unit,
    onPairWithCodeClick: () -> Unit,
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
                    .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                    .pixelFrame(MaterialTheme.pixel.rule),
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
                text = "Install mobaide-agent on a host machine. The agent will display " +
                    "a pairing QR after install — scan it here to finish setup.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
            Text(
                text = "1. Install on your host",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            PixelButton(
                style = PixelButtonStyle.Primary,
                onClick = onShowInstallClick,
                contentPadding = ButtonDefaults.ButtonWithIconContentPadding,
            ) {
                Icon(
                    Icons.Default.Terminal,
                    contentDescription = null,
                    modifier = Modifier.size(ButtonDefaults.IconSize),
                )
                Spacer(modifier = Modifier.width(ButtonDefaults.IconSpacing))
                Text("Show install commands")
            }
            Text(
                text = "2. Scan the pairing QR shown after install",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
            PixelButton(
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
            // The path that works when the host's screen is not somewhere a
            // camera can see it — an SSH session, a VM console, another room.
            PixelTextButton(onClick = onPairWithCodeClick) {
                Text("Pair with a code instead")
            }
            PixelTextButton(onClick = onAddManuallyClick) {
                Text("Add manually instead")
            }
        }
    }
}

/**
 * Dialogs for the reversed direction.
 *
 * There is no "waiting for the agent to come online" case here — the flow
 * hands that shared tail to [QrPairingDialogs] once the confirm lands.
 */
@Composable
internal fun DeviceCodePairingDialogs(
    state: DeviceCodePairingState,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    when (state) {
        DeviceCodePairingState.Idle -> Unit
        DeviceCodePairingState.Minting -> PixelAlertDialog(
            onDismissRequest = {},
            title = { Text("Getting a code…") },
            text = {
                Box(
                    modifier = Modifier.fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    PixelBlinkCursor()
                }
            },
            confirmButton = {},
        )
        is DeviceCodePairingState.WaitingForHost -> PixelAlertDialog(
            onDismissRequest = onDismiss,
            title = { Text("Add an agent") },
            text = {
                Column {
                    Text("On the computer you want to pair, run:")
                    Spacer(modifier = Modifier.size(8.dp))
                    CopyableCodeBlock(
                        label = "Pairing command",
                        code = state.command,
                        modifier = Modifier.testTag("device-code-command"),
                    )
                    Spacer(modifier = Modifier.size(12.dp))
                    Text(
                        "The code is ${state.code} if you would rather type it " +
                            "yourself. The agent will name this account and ask you " +
                            "to confirm before it pairs — only enter it on a machine " +
                            "you own.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(modifier = Modifier.size(12.dp))
                    Box(
                        modifier = Modifier.fillMaxWidth(),
                        contentAlignment = Alignment.Center,
                    ) {
                        PixelBlinkCursor()
                    }
                    Spacer(modifier = Modifier.size(8.dp))
                    Text(
                        "Waiting for a machine to enter the code…",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            },
            confirmButton = {},
            dismissButton = {
                PixelTextButton(onClick = onDismiss) { Text("Cancel") }
            },
        )
        is DeviceCodePairingState.Confirming -> PixelAlertDialog(
            onDismissRequest = onDismiss,
            title = {
                Text(
                    if (state.repairAgentName == null) {
                        "Is this your machine?"
                    } else {
                        "Move '${state.repairAgentName}' to this machine?"
                    }
                )
            },
            text = {
                Column {
                    Text(
                        state.hostname,
                        style = MaterialTheme.typography.titleMedium,
                    )
                    state.os?.let {
                        Text(it, style = MaterialTheme.typography.bodySmall)
                    }
                    Spacer(modifier = Modifier.size(12.dp))
                    // The name above is whatever the machine that entered the
                    // code reported, over an endpoint that authenticates
                    // nobody — it helps a user recognise their own host and
                    // proves nothing by itself.
                    Text(
                        "A machine calling itself ${state.hostname} entered your " +
                            "code. If that is not the computer you just typed it " +
                            "into, do not continue.",
                    )
                    state.repairAgentName?.let { name ->
                        Spacer(modifier = Modifier.size(12.dp))
                        Text(
                            "That machine reported it is already '$name'. Continuing " +
                                "rotates that agent's credentials onto it — if the " +
                                "real '$name' is a different computer, that one goes " +
                                "offline.",
                        )
                    }
                    state.blockedReason?.let {
                        Spacer(modifier = Modifier.size(12.dp))
                        Text(
                            it.message,
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
                }
            },
            confirmButton = {
                PixelTextButton(
                    onClick = onConfirm,
                    enabled = state.blockedReason == null,
                ) { Text("Pair this machine") }
            },
            dismissButton = {
                PixelTextButton(onClick = onDismiss) { Text("Cancel") }
            },
        )
        is DeviceCodePairingState.Claiming -> PixelAlertDialog(
            onDismissRequest = {},
            title = { Text("Pairing…") },
            text = {
                Box(
                    modifier = Modifier.fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    PixelBlinkCursor()
                }
            },
            confirmButton = {},
        )
        is DeviceCodePairingState.Error -> PixelAlertDialog(
            onDismissRequest = onDismiss,
            title = { Text("Pairing failed") },
            text = { Text(state.message) },
            confirmButton = {
                PixelButton(onClick = onDismiss) { Text("Close") }
            },
        )
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
        is QrPairingState.Confirming -> PixelAlertDialog(
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
                PixelTextButton(onClick = onConfirm) { Text("Pair") }
            },
            dismissButton = {
                PixelTextButton(onClick = onDismiss) { Text("Cancel") }
            },
        )
        is QrPairingState.Claiming -> PixelAlertDialog(
            onDismissRequest = {},
            title = { Text("Pairing…") },
            text = {
                Box(
                    modifier = Modifier.fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    PixelBlinkCursor()
                }
            },
            confirmButton = {},
        )
        is QrPairingState.WaitingForOnline -> PixelAlertDialog(
            // The watcher coroutine owns the timeout — letting the user
            // dismiss only abandons the wait, it does not cancel the
            // server-side claim (which has already succeeded). The dismiss
            // button is wired so a user who wants to stop watching can.
            onDismissRequest = onDismiss,
            title = { Text("Waiting for agent…") },
            text = {
                Column {
                    Text(
                        "Pairing request accepted. Waiting for " +
                            "${state.friendlyName} (${state.agentId.take(8)}…) " +
                            "to come online.",
                    )
                    Spacer(modifier = Modifier.size(12.dp))
                    Box(
                        modifier = Modifier.fillMaxWidth(),
                        contentAlignment = Alignment.Center,
                    ) {
                        PixelBlinkCursor()
                    }
                    Spacer(modifier = Modifier.size(12.dp))
                    Text(
                        "Elapsed: ${state.elapsedSeconds}s / " +
                            "$WAIT_FOR_ONLINE_TIMEOUT_SECONDS s",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            },
            confirmButton = {},
            dismissButton = {
                PixelTextButton(onClick = onDismiss) { Text("Stop waiting") }
            },
        )
        is QrPairingState.Success -> PixelAlertDialog(
            onDismissRequest = onDismiss,
            title = { Text("Paired") },
            text = {
                Text(
                    "Agent ${state.friendlyName} (${state.agentId}) " +
                    "has been paired to your account.",
                )
            },
            confirmButton = {
                PixelTextButton(onClick = onDismiss) { Text("OK") }
            },
        )
        is QrPairingState.Error -> PixelAlertDialog(
            onDismissRequest = onDismiss,
            title = { Text("Pairing failed") },
            text = { Text(state.message) },
            confirmButton = {
                PixelButton(onClick = onDismiss) { Text("Close") }
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
        border = BorderStroke(2.dp, MaterialTheme.pixel.surface2),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val statusColor = if (agent.online) MaterialTheme.pixel.statusRunning else MaterialTheme.pixel.statusStopped
            PixelStatusDot(color = statusColor)

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
                    fontFamily = ChromeFontFamily,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = if (agent.online) "Online" else "Offline",
                    style = MaterialTheme.typography.labelSmall,
                    color = if (agent.online) MaterialTheme.pixel.statusRunning else MaterialTheme.pixel.statusStopped,
                )
            }

            if (isConnected) {
                Icon(
                    Icons.Default.CheckCircle,
                    contentDescription = "Connected",
                    tint = MaterialTheme.pixel.statusRunning,
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
