package com.mobaide.app.ui.projects.git

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Checklist
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.OutlinedTextField
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import com.mobaide.app.ui.common.pixel.PixelIconButton
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.PixelScaffold
import androidx.compose.material3.SnackbarDuration
import com.mobaide.app.ui.common.pixel.PixelSnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.PixelTopBar
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
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.ui.common.DeleteSwipeBackground

const val GIT_NEW_BRANCH_BUTTON_TAG = "git_new_branch_button"
const val GIT_FILTER_TAG = "git_filter"
const val GIT_REMOTE_HEADER_TAG = "git_remote_header"

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

    PixelScaffold(
        topBar = {
            if (state.inSelectionMode) {
                PixelTopBar(
                    title = { Text("${state.selected.size} selected") },
                    navigationIcon = {
                        IconButton(onClick = viewModel::exitSelection) {
                            Icon(Icons.Default.Close, contentDescription = "Exit selection")
                        }
                    },
                    actions = {
                        PixelIconButton(
                            imageVector = Icons.Default.Delete,
                            contentDescription = "Delete selected",
                            onClick = viewModel::requestDelete,
                            enabled = state.selected.isNotEmpty() && !state.deleting,
                        )
                    },
                )
            } else {
                PixelTopBar(
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
                        if (state.branchesEnabled) {
                            PixelIconButton(
                                imageVector = Icons.Default.Add,
                                contentDescription = "New branch",
                                onClick = viewModel::openNewBranch,
                                enabled = !state.rowBusy,
                                modifier = Modifier.testTag(GIT_NEW_BRANCH_BUTTON_TAG),
                            )
                        }
                        if (state.hasDeletableItems) {
                            PixelIconButton(
                                imageVector = Icons.Default.Checklist,
                                contentDescription = "Select items",
                                onClick = viewModel::enterSelectionMode,
                            )
                        }
                    },
                )
            }
        },
        snackbarHost = { PixelSnackbarHost(snackbarHostState) },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            val visibleItems = state.visibleItems
            when {
                state.isLoading && visibleItems.isEmpty() -> {
                    PixelBlinkCursor(modifier = Modifier.align(Alignment.Center))
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
                    val localItems = state.localItems
                    val remoteItems = state.remoteItems
                    LazyColumn(
                        contentPadding = PaddingValues(vertical = 8.dp),
                        verticalArrangement = Arrangement.spacedBy(2.dp),
                    ) {
                        if (state.filterable) {
                            item(key = "filter") {
                                OutlinedTextField(
                                    colors = PixelTextFieldDefaults.colors(),
                                    value = state.filter,
                                    onValueChange = viewModel::setFilter,
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(horizontal = 12.dp, vertical = 4.dp)
                                        .testTag(GIT_FILTER_TAG),
                                    placeholder = { Text("Filter branches") },
                                    singleLine = true,
                                )
                            }
                        }
                        if (localItems.isEmpty() && remoteItems.isEmpty()) {
                            item(key = "no-match") {
                                Text(
                                    text = "No matching branches or worktrees.",
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(24.dp),
                                )
                            }
                        }
                        items(localItems, key = { it.id }) { item ->
                            SwipeableGitRow(item = item, state = state, viewModel = viewModel)
                        }
                        if (remoteItems.isNotEmpty()) {
                            item(key = "remote-header") {
                                RemoteSectionHeader(
                                    count = remoteItems.size,
                                    open = state.remoteOpen,
                                    onToggle = viewModel::toggleRemote,
                                )
                            }
                            if (state.remoteOpen) {
                                items(remoteItems, key = { it.id }) { item ->
                                    SwipeableGitRow(item = item, state = state, viewModel = viewModel)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    val checkoutPrompt = state.checkoutPrompt
    if (checkoutPrompt != null) {
        val branch = primaryLabel(checkoutPrompt)
        PixelAlertDialog(
            onDismissRequest = viewModel::dismissCheckoutPrompt,
            title = { Text("Check out $branch?") },
            text = { Text(checkoutConfirmMessage(branch, projectName)) },
            confirmButton = {
                PixelTextButton(onClick = viewModel::confirmCheckout) { Text("Check out") }
            },
            dismissButton = {
                PixelTextButton(onClick = viewModel::dismissCheckoutPrompt) { Text("Cancel") }
            },
        )
    }

    val newBranch = state.newBranch
    if (newBranch != null) {
        GitNewBranchSheet(
            state = newBranch,
            items = state.visibleItems,
            projectName = projectName,
            onDismiss = viewModel::dismissNewBranch,
            onCreate = viewModel::createBranch,
        )
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
        PixelAlertDialog(
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
                PixelTextButton(onClick = viewModel::confirmForceDelete) { Text("Force delete") }
            },
            dismissButton = {
                PixelTextButton(onClick = viewModel::dismissForceDeletePrompt) { Text("Cancel") }
            },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SwipeableGitRow(
    item: GitItem,
    state: GitUiState,
    viewModel: GitViewModel,
) {
    val row: @Composable () -> Unit = {
        GitItemRow(
            item = item,
            selected = item.id in state.selected,
            inSelectionMode = state.inSelectionMode,
            onTap = {
                if (state.inSelectionMode) viewModel.toggle(item.id)
            },
            onLongPress = { viewModel.enterSelection(item.id) },
            checkoutEnabled = state.branchesEnabled,
            busy = state.rowBusy,
            onCheckout = { viewModel.requestCheckout(item.id) },
        )
    }
    if (item.isDeletable && !state.inSelectionMode) {
        val dismissState = rememberSwipeToDismissBoxState(
            confirmValueChange = { value ->
                if (value == SwipeToDismissBoxValue.EndToStart) {
                    viewModel.deleteItem(item.id)
                }
                // Return false so the row isn't locked in the dismissed
                // position: the VM hides it via pendingDeleteIds, and Undo
                // must restore it.
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

@Composable
private fun RemoteSectionHeader(
    count: Int,
    open: Boolean,
    onToggle: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onToggle)
            .padding(horizontal = 16.dp, vertical = 12.dp)
            .testTag(GIT_REMOTE_HEADER_TAG),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(
            text = "Remote branches ($count)",
            style = MaterialTheme.typography.titleSmall.copy(fontFamily = ChromeFontFamily),
            fontWeight = FontWeight.Medium,
            color = MaterialTheme.colorScheme.primary,
        )
        Icon(
            imageVector = if (open) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
            contentDescription = if (open) "Collapse remote branches" else "Expand remote branches",
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun BusyConflictDialog(
    holders: List<BusyHolder>,
    onDismiss: () -> Unit,
) {
    PixelAlertDialog(
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
            PixelTextButton(onClick = onDismiss) { Text("OK") }
        },
    )
}
