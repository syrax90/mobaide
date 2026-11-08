package com.mobaide.app.ui.projects

import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import androidx.compose.material3.ExperimentalMaterial3Api
import com.mobaide.app.ui.common.pixel.PixelFab
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.ui.common.DeleteSwipeBackground
import com.mobaide.app.ui.common.DragHandleIcon
import kotlinx.coroutines.withTimeoutOrNull
import sh.calvin.reorderable.ReorderableItem
import sh.calvin.reorderable.rememberReorderableLazyListState

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProjectsScreen(
    viewModel: ProjectsViewModel,
    onBack: () -> Unit,
    onOpenGit: (String) -> Unit = {},
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val pathSuggestions by viewModel.pathSuggestionState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(viewModel) {
        viewModel.events.collect { event ->
            when (event) {
                is ProjectsUiEvent.DeletedWithUndo -> {
                    // Material3's SnackbarDuration.Short is 4s and Long is 10s; use Indefinite
                    // racing a 5s timeout to hit the exact 5s UNDO window.
                    val result = withTimeoutOrNull(5_000L) {
                        snackbarHostState.showSnackbar(
                            message = "Deleted '${event.snapshot.name}'",
                            actionLabel = "Undo",
                            withDismissAction = true,
                            duration = SnackbarDuration.Indefinite,
                        )
                    }
                    snackbarHostState.currentSnackbarData?.dismiss()
                    if (result == SnackbarResult.ActionPerformed) {
                        viewModel.undoDelete(event.snapshot)
                    }
                }
                ProjectsUiEvent.LinearKeyReminder -> {
                    snackbarHostState.showSnackbar("Restored — re-enter Linear API key in Edit.")
                }
                is ProjectsUiEvent.Message -> {
                    snackbarHostState.showSnackbar(event.text)
                }
                ProjectsUiEvent.CloseSheet -> Unit  // state.form already cleared by VM
            }
        }
    }

    PixelScaffold(
        topBar = {
            PixelTopBar(
                title = { Text("Projects") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Back",
                        )
                    }
                },
            )
        },
        floatingActionButton = {
            PixelFab(onClick = viewModel::openCreateSheet) {
                Icon(Icons.Default.Add, contentDescription = "New project")
            }
        },
        snackbarHost = { PixelSnackbarHost(snackbarHostState) },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            if (state.isLoading && state.projects.isEmpty()) {
                PixelBlinkCursor(
                    modifier = Modifier.align(Alignment.Center),
                    height = 24.dp,
                )
            } else if (state.projects.isEmpty()) {
                Text(
                    text = "No projects yet. Tap + to create one.",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.align(Alignment.Center),
                )
            } else {
                ProjectList(
                    projects = state.projects,
                    busy = state.busyProjectNames,
                    onOpen = viewModel::openEditSheet,
                    onSwipeDelete = viewModel::onSwipeDelete,
                    onReorder = viewModel::reorder,
                )
            }
        }
    }

    val form = state.form
    if (form != null) {
        ProjectFormSheet(
            state = form,
            onDismiss = viewModel::closeSheet,
            onNameChange = viewModel::onNameChange,
            onPathChange = viewModel::onPathChange,
            onCondaChange = viewModel::onCondaChange,
            onLinearKeyChange = viewModel::onLinearKeyChange,
            onToggleKeyVisible = viewModel::toggleLinearKeyVisible,
            onRequestClearLinearKey = viewModel::requestClearLinearKey,
            onTouch = viewModel::touch,
            onAddFlag = viewModel::addFlag,
            onUpdateFlag = viewModel::updateFlag,
            onRemoveFlag = viewModel::removeFlag,
            onToggleTriggerLabel = viewModel::toggleTriggerLabel,
            onToggleTriggerState = viewModel::toggleTriggerState,
            onActiveStateChange = viewModel::onActiveStateChange,
            onDoneStateChange = viewModel::onDoneStateChange,
            onRemovalLabelChange = viewModel::onRemovalLabelChange,
            onRefreshFilterMetadata = viewModel::refreshFilterMetadata,
            onSave = viewModel::save,
            pathSuggestions = pathSuggestions,
            onPathFocusChange = viewModel::onPathFocusChange,
            onPickPathSuggestion = viewModel::pickPathSuggestion,
            onOpenGit = { name ->
                viewModel.closeSheet()
                onOpenGit(name)
            },
        )
    }

    val pending = state.pendingDelete
    if (pending != null) {
        PixelAlertDialog(
            onDismissRequest = viewModel::dismissBusyDelete,
            title = { Text("Project is in use") },
            text = {
                Text(
                    "'${pending.name}' has an active auto-scan. The server will refuse deletion. " +
                        "Stop the scan first, or continue to attempt anyway."
                )
            },
            confirmButton = {
                PixelTextButton(onClick = viewModel::confirmBusyDelete) { Text("Continue") }
            },
            dismissButton = {
                PixelTextButton(onClick = viewModel::dismissBusyDelete) { Text("Cancel") }
            },
        )
    }

}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ProjectList(
    projects: List<ProjectInfo>,
    busy: Set<String>,
    onOpen: (ProjectInfo) -> Unit,
    onSwipeDelete: (ProjectInfo) -> Unit,
    onReorder: (List<String>) -> Unit,
) {
    val lazyListState = rememberLazyListState()
    // Local display list updated synchronously in `onMove` so the Reorderable library
    // sees the new order within the same frame; it is saved once, on drop. The VM's
    // StateFlow remains the source of truth; LaunchedEffect re-syncs on every upstream
    // change (including server reverts), except during a drag.
    var display by remember { mutableStateOf(projects) }

    val reorderState = rememberReorderableLazyListState(lazyListState) { from, to ->
        val current = display.toMutableList()
        val moved = current.removeAt(from.index)
        current.add(to.index, moved)
        display = current
    }
    LaunchedEffect(projects, reorderState.isAnyItemDragging) {
        if (!reorderState.isAnyItemDragging) display = projects
    }
    // The handle's gesture keeps the callback from its first touch, so read the
    // current list through these rather than through the captured parameters.
    val latestProjects by rememberUpdatedState(projects)
    val latestOnReorder by rememberUpdatedState(onReorder)
    // One PATCH per drop, not one per swap: concurrent PATCHes of intermediate
    // orders could land out of order and persist one the user dragged past.
    val commitDrop = {
        val names = display.map { it.name }
        if (names != latestProjects.map { it.name }) latestOnReorder(names)
    }
    // TalkBack's way to reorder, since a screen-reader user cannot drag.
    val moveBy = { from: Int, delta: Int ->
        display = display.toMutableList().apply { add(from + delta, removeAt(from)) }
        commitDrop()
    }

    LazyColumn(
        state = lazyListState,
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        itemsIndexed(items = display, key = { _, it -> it.name }) { index, project ->
            ReorderableItem(reorderState, key = project.name) { _ ->
                // Captured here because `draggableHandle` is only available
                // within the ReorderableCollectionItemScope receiver. The
                // resulting Modifier is pure data and can be applied deeper
                // in the tree — in this case, to the drag-handle icon.
                val handleModifier = Modifier.draggableHandle(onDragStopped = { commitDrop() })
                val dismissState = rememberSwipeToDismissBoxState(
                    confirmValueChange = { value ->
                        if (value == SwipeToDismissBoxValue.EndToStart) {
                            onSwipeDelete(project)
                        }
                        // Return false so SwipeToDismissBox doesn't lock the card in the
                        // dismissed position. The VM drives list updates via the
                        // projects.changed event, and UNDO must be able to restore the card.
                        false
                    },
                )
                SwipeToDismissBox(
                    state = dismissState,
                    enableDismissFromStartToEnd = false,
                    enableDismissFromEndToStart = true,
                    backgroundContent = { DeleteSwipeBackground() },
                ) {
                    ProjectCard(
                        project = project,
                        busy = busy.contains(project.name),
                        onClick = { onOpen(project) },
                        dragHandle = {
                            // The whole 48dp box takes the drag, not just the icon.
                            Box(
                                modifier = Modifier.size(48.dp).then(handleModifier),
                                contentAlignment = Alignment.Center,
                            ) {
                                DragHandleIcon()
                            }
                        },
                        modifier = Modifier.semantics {
                            customActions = buildList {
                                if (index > 0) {
                                    add(CustomAccessibilityAction("Move up") { moveBy(index, -1); true })
                                }
                                if (index < display.lastIndex) {
                                    add(CustomAccessibilityAction("Move down") { moveBy(index, 1); true })
                                }
                            }
                        },
                    )
                }
            }
        }
    }
}
