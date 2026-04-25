package com.claudemobile.app.ui.projects.git

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.claudemobile.app.data.network.dto.BusyHolder
import kotlinx.coroutines.withTimeoutOrNull

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GitScreen(
    projectName: String,
    viewModel: GitViewModel,
    onBack: () -> Unit,
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(viewModel) {
        viewModel.events.collect { event ->
            when (event) {
                is GitUiEvent.DeletedWithUndo -> {
                    val result = withTimeoutOrNull(5_000L) {
                        snackbarHostState.showSnackbar(
                            message = "Deleting ${event.requestItems.size} item(s)…",
                            actionLabel = "Undo",
                            withDismissAction = true,
                            duration = SnackbarDuration.Indefinite,
                        )
                    }
                    snackbarHostState.currentSnackbarData?.dismiss()
                    if (result != SnackbarResult.ActionPerformed) {
                        viewModel.confirmDelete(event.requestItems)
                    }
                }
                is GitUiEvent.Message -> {
                    snackbarHostState.showSnackbar(event.text)
                }
                is GitUiEvent.DeleteResults -> {
                    val failed = event.results.count { !it.ok && it.error != "force_required" }
                    if (failed > 0) {
                        snackbarHostState.showSnackbar("$failed item(s) failed to delete")
                    }
                }
            }
        }
    }

    BackHandler(enabled = state.inSelectionMode) { viewModel.exitSelection() }

    Scaffold(
        topBar = {
            if (state.inSelectionMode) {
                TopAppBar(
                    title = { Text("${state.selected.size} selected") },
                    navigationIcon = {
                        IconButton(onClick = viewModel::exitSelection) {
                            Icon(Icons.Default.Close, contentDescription = "Exit selection")
                        }
                    },
                    actions = {
                        IconButton(
                            onClick = viewModel::requestDelete,
                            enabled = state.selected.isNotEmpty() && !state.deleting,
                        ) {
                            Icon(Icons.Default.Delete, contentDescription = "Delete selected")
                        }
                    },
                )
            } else {
                TopAppBar(
                    title = { Text("Git: $projectName") },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(
                                Icons.AutoMirrored.Filled.ArrowBack,
                                contentDescription = "Back",
                            )
                        }
                    },
                )
            }
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            when {
                state.isLoading && state.items.isEmpty() -> {
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                }
                state.errorMessage != null && state.items.isEmpty() -> {
                    Text(
                        text = state.errorMessage.orEmpty(),
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.align(Alignment.Center).padding(24.dp),
                    )
                }
                state.items.isEmpty() -> {
                    Text(
                        text = "No branches or worktrees.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.align(Alignment.Center),
                    )
                }
                else -> {
                    LazyColumn(
                        contentPadding = PaddingValues(vertical = 8.dp),
                        verticalArrangement = Arrangement.spacedBy(2.dp),
                    ) {
                        items(state.items, key = { it.id }) { item ->
                            GitItemRow(
                                item = item,
                                selected = item.id in state.selected,
                                inSelectionMode = state.inSelectionMode,
                                onTap = {
                                    if (state.inSelectionMode) viewModel.toggle(item.id)
                                },
                                onLongPress = { viewModel.enterSelection(item.id) },
                            )
                        }
                    }
                }
            }
        }
    }

    val busyDialog = state.busyDialog
    if (busyDialog != null) {
        BusyConflictDialog(
            holders = busyDialog,
            onDismiss = viewModel::dismissBusyDialog,
        )
    }

    val forcePrompt = state.forceDeletePrompt
    if (forcePrompt != null) {
        AlertDialog(
            onDismissRequest = viewModel::dismissForceDeletePrompt,
            title = { Text("Branch not fully merged") },
            text = {
                Text(
                    "These refs are not fully merged: ${forcePrompt.refs.joinToString(", ")}." +
                        " Force-delete will discard unmerged commits.",
                )
            },
            confirmButton = {
                TextButton(onClick = viewModel::confirmForceDelete) { Text("Force delete") }
            },
            dismissButton = {
                TextButton(onClick = viewModel::dismissForceDeletePrompt) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun BusyConflictDialog(
    holders: List<BusyHolder>,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Item is in use") },
        text = {
            val lines = holders.map { holder ->
                when (holder.kind) {
                    "session" -> "Session ${holder.session_id ?: "?"}: ${holder.cwd ?: "?"}"
                    "auto_scan" -> "Auto-scan ${holder.project_name ?: "?"}: ${holder.worktree_path ?: "?"}"
                    else -> "${holder.kind}: ${holder.cwd ?: holder.worktree_path ?: "?"}"
                }
            }
            Text("Stop the holder, then try again.\n\n" + lines.joinToString("\n"))
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("OK") }
        },
    )
}
