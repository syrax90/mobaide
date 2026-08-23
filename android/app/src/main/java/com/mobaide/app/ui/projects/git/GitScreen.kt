package com.mobaide.app.ui.projects.git

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
import androidx.compose.material.icons.filled.Checklist
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
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.BusyHolder
import com.mobaide.app.ui.common.DeleteSwipeBackground

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
                is GitUiEvent.Message -> snackbarHostState.showSnackbar(event.text)
            }
        }
    }

    // The undo window is owned by the ViewModel: when it elapses, `undoPrompt`
    // clears, this effect is cancelled and the snackbar goes away without either
    // branch running. A second delete raises a new id, replacing the snackbar
    // with the coalesced count.
    val undoPrompt = state.undoPrompt
    LaunchedEffect(undoPrompt?.id) {
        if (undoPrompt == null) return@LaunchedEffect
        val result = snackbarHostState.showSnackbar(
            message = "Deleting ${undoPrompt.count} item(s)…",
            actionLabel = "Undo",
            withDismissAction = true,
            duration = SnackbarDuration.Indefinite,
        )
        if (result == SnackbarResult.ActionPerformed) viewModel.undo() else viewModel.commitNow()
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
                    actions = {
                        if (state.hasDeletableItems) {
                            IconButton(onClick = viewModel::enterSelectionMode) {
                                Icon(Icons.Default.Checklist, contentDescription = "Select items")
                            }
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
            val visibleItems = state.visibleItems
            when {
                state.isLoading && visibleItems.isEmpty() -> {
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                }
                state.errorMessage != null && visibleItems.isEmpty() -> {
                    Text(
                        text = state.errorMessage.orEmpty(),
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.align(Alignment.Center).padding(24.dp),
                    )
                }
                visibleItems.isEmpty() -> {
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
                        items(visibleItems, key = { it.id }) { item ->
                            val row: @Composable () -> Unit = {
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
                            if (item.isDeletable && !state.inSelectionMode) {
                                val dismissState = rememberSwipeToDismissBoxState(
                                    confirmValueChange = { value ->
                                        if (value == SwipeToDismissBoxValue.EndToStart) {
                                            viewModel.deleteItem(item.id)
                                        }
                                        // Return false so the row isn't locked in the
                                        // dismissed position: the VM hides it via
                                        // pendingDeleteIds, and Undo must restore it.
                                        false
                                    },
                                )
                                SwipeToDismissBox(
                                    state = dismissState,
                                    enableDismissFromStartToEnd = false,
                                    enableDismissFromEndToStart = true,
                                    backgroundContent = { DeleteSwipeBackground() },
                                ) {
                                    row()
                                }
                            } else {
                                row()
                            }
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
            title = { Text("Force delete?") },
            text = {
                Text(
                    "These items have unmerged commits or uncommitted changes and" +
                        " can't be deleted normally: ${forcePrompt.refs.joinToString(", ")}." +
                        " Force-delete permanently discards that work.",
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
                    "session" -> "Session: ${holder.cwd ?: "?"}"
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
