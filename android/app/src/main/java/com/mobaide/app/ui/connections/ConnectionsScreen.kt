package com.mobaide.app.ui.connections

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Lan
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.auth.ConnectionStore.ConnectionProfile

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConnectionsScreen(
    viewModel: ConnectionsViewModel,
    onBack: () -> Unit,
    onAddConnection: () -> Unit,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Connections") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
        floatingActionButton = {
            FloatingActionButton(onClick = onAddConnection) {
                Icon(Icons.Default.Add, contentDescription = "Add connection")
            }
        },
    ) { padding ->
        if (uiState.profiles.isEmpty()) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(32.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Text(
                    text = "No saved connections",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
            ) {
                items(uiState.profiles, key = { it.id }) { profile ->
                    ConnectionItem(
                        profile = profile,
                        isActive = profile.id == uiState.activeProfileId,
                        onClick = { viewModel.switchProfile(profile.id) },
                        onDelete = { viewModel.removeProfile(profile.id) },
                    )
                    HorizontalDivider()
                }
            }
        }
    }
}

@Composable
private fun ConnectionItem(
    profile: ConnectionProfile,
    isActive: Boolean,
    onClick: () -> Unit,
    onDelete: () -> Unit,
) {
    ListItem(
        modifier = Modifier.clickable(onClick = onClick),
        headlineContent = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(profile.label)
                if (isActive) {
                    Spacer(Modifier.width(8.dp))
                    Icon(
                        Icons.Default.CheckCircle,
                        contentDescription = "Active",
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(16.dp),
                    )
                }
            }
        },
        supportingContent = {
            Text(
                when (profile.mode) {
                    "relay" -> "Relay: ${profile.relayServerUrl ?: "unknown"}"
                    else -> "Direct: ${profile.agentUrl ?: "unknown"}"
                },
            )
        },
        leadingContent = {
            Icon(
                when (profile.mode) {
                    "relay" -> Icons.Default.Cloud
                    else -> Icons.Default.Lan
                },
                contentDescription = profile.mode,
            )
        },
        trailingContent = {
            if (!isActive) {
                IconButton(onClick = onDelete) {
                    Icon(
                        Icons.Default.Delete,
                        contentDescription = "Remove",
                        tint = MaterialTheme.colorScheme.error,
                    )
                }
            }
        },
    )
}
