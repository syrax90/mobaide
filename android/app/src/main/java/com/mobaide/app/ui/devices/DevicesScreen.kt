package com.mobaide.app.ui.devices

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.DeviceResponse

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DevicesScreen(
    viewModel: DevicesViewModel,
    onBack: () -> Unit,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(uiState.error) {
        uiState.error?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearError()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("My devices") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Back",
                        )
                    }
                },
                actions = {
                    IconButton(onClick = { viewModel.refresh() }) {
                        Icon(Icons.Default.Refresh, contentDescription = "Refresh")
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        DevicesContent(
            padding = padding,
            devices = uiState.devices,
            currentDeviceId = uiState.currentDeviceId,
            isLoading = uiState.isLoading,
            onRefresh = viewModel::refresh,
            onRevokeClick = viewModel::showRevokeConfirm,
        )
    }

    uiState.revokeTarget?.let { target ->
        val isCurrent = target.device_id == uiState.currentDeviceId
        AlertDialog(
            onDismissRequest = viewModel::dismissRevokeConfirm,
            title = { Text("Revoke device?") },
            text = {
                Text(
                    if (isCurrent) {
                        "Revoking this device will sign you out on the next " +
                            "token refresh. You can sign back in afterwards. " +
                            "Every agent paired with it is signed out too."
                    } else {
                        "${target.name ?: target.device_id} will be signed out " +
                            "on its next token refresh. Every agent paired " +
                            "with it is signed out too."
                    }
                )
            },
            confirmButton = {
                TextButton(onClick = { viewModel.confirmRevoke(target) }) {
                    Text("Revoke")
                }
            },
            dismissButton = {
                TextButton(onClick = viewModel::dismissRevokeConfirm) {
                    Text("Cancel")
                }
            },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DevicesContent(
    padding: PaddingValues,
    devices: List<DeviceResponse>,
    currentDeviceId: String?,
    isLoading: Boolean,
    onRefresh: () -> Unit,
    onRevokeClick: (DeviceResponse) -> Unit,
) {
    PullToRefreshBox(
        isRefreshing = isLoading,
        onRefresh = onRefresh,
        modifier = Modifier.fillMaxSize().padding(padding),
    ) {
        when {
            isLoading && devices.isEmpty() -> {
                Box(modifier = Modifier.fillMaxSize()) {
                    CircularProgressIndicator(
                        modifier = Modifier.align(Alignment.Center),
                    )
                }
            }
            devices.isEmpty() -> {
                Box(modifier = Modifier.fillMaxSize()) {
                    Text(
                        text = "No devices yet. Pair an agent to register this device.",
                        modifier = Modifier
                            .align(Alignment.Center)
                            .padding(horizontal = 24.dp),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
            else -> {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    items(devices, key = { it.device_id }) { device ->
                        DeviceCard(
                            device = device,
                            isCurrent = device.device_id == currentDeviceId,
                            onRevokeClick = { onRevokeClick(device) },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun DeviceCard(
    device: DeviceResponse,
    isCurrent: Boolean,
    onRevokeClick: () -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = device.name ?: "Unnamed device",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    if (isCurrent) {
                        Text(
                            text = "This device",
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.primary,
                        )
                    }
                }
                OutlinedButton(onClick = onRevokeClick) {
                    Text("Revoke")
                }
            }
            Spacer(modifier = Modifier.height(8.dp))
            HorizontalDivider()
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = "Device id",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = device.device_id,
                style = MaterialTheme.typography.bodySmall,
                fontFamily = FontFamily.Monospace,
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = "Added: ${device.created_at}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = "Last seen: ${device.last_seen_at ?: "—"}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
