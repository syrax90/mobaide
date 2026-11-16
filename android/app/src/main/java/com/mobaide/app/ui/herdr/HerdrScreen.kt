package com.mobaide.app.ui.herdr

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.HerdrCreateRequest
import com.mobaide.app.data.network.dto.HerdrFeatures
import com.mobaide.app.data.network.dto.HerdrPane
import com.mobaide.app.data.network.dto.HerdrServer
import com.mobaide.app.data.network.dto.HerdrTab
import com.mobaide.app.data.network.dto.HerdrTarget
import com.mobaide.app.data.network.dto.HerdrWorkspace
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.ui.common.LoadingIndicator
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelCard
import com.mobaide.app.ui.common.pixel.PixelEmptyState
import com.mobaide.app.ui.common.pixel.PixelIconButton
import com.mobaide.app.ui.common.pixel.PixelScaffold
import com.mobaide.app.ui.common.pixel.PixelSnackbarHost
import com.mobaide.app.ui.common.pixel.PixelTopBar
import com.mobaide.app.ui.sessions.SessionAttentionChip
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme
import kotlinx.coroutines.flow.collectLatest

const val HERDR_SCREEN_TAG = "herdr_screen"
const val HERDR_LOADING_TAG = "herdr_loading"
const val HERDR_EMPTY_TAG = "herdr_empty"
const val HERDR_PANE_ROW_TAG = "herdr_pane_row"
const val HERDR_PANE_OPENING_TAG = "herdr_pane_opening"

/** Which dialog the dashboard shows. One at a time. */
private sealed interface HerdrDialog {
    data class Create(val server: HerdrServer, val workspaceId: String?) : HerdrDialog
    data class Rename(val server: HerdrServer, val target: String, val id: String, val current: String) : HerdrDialog
    data class Close(val server: HerdrServer, val target: String, val id: String, val label: String) : HerdrDialog
    data class StartAgent(val server: HerdrServer, val paneId: String) : HerdrDialog
}

/** What a row of the dashboard can ask for. */
private sealed interface HerdrAction {
    data class Open(val row: HerdrPaneRow) : HerdrAction
    data class Seen(val row: HerdrPaneRow) : HerdrAction
    data class Split(val row: HerdrPaneRow, val placement: String) : HerdrAction
    data class Ask(val dialog: HerdrDialog) : HerdrAction
}

/** The Herdr dashboard: the agents that need the user, then every server's tree. */
@Composable
fun HerdrScreen(
    onBack: () -> Unit,
    onOpenPane: (sessionId: String, serverId: String, paneId: String) -> Unit,
    modifier: Modifier = Modifier,
    viewModel: HerdrViewModel = hiltViewModel(),
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val projects by viewModel.projects.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    var dialog by remember { mutableStateOf<HerdrDialog?>(null) }

    LaunchedEffect(viewModel) {
        viewModel.events.collectLatest { event ->
            when (event) {
                is HerdrEvent.OpenPane -> {
                    dialog = null
                    onOpenPane(event.sessionId, event.serverId, event.paneId)
                }
                is HerdrEvent.ShowMessage -> snackbarHostState.showSnackbar(event.text)
                HerdrEvent.ActionDone -> dialog = null
            }
        }
    }

    HerdrContent(
        state = state,
        snackbarHostState = snackbarHostState,
        onBack = onBack,
        onAction = { action ->
            when (action) {
                is HerdrAction.Open -> viewModel.openPane(action.row.server.server_id, action.row.pane.pane_id)
                is HerdrAction.Seen -> viewModel.markSeen(action.row)
                is HerdrAction.Split -> viewModel.create(
                    HerdrCreateRequest(
                        server_id = action.row.server.server_id,
                        placement = action.placement,
                        pane_id = action.row.pane.pane_id,
                    ),
                    fromDialog = false,
                )
                is HerdrAction.Ask -> {
                    viewModel.clearActionError()
                    dialog = action.dialog
                }
            }
        },
        modifier = modifier,
    )

    val busy = (state as? HerdrUiState.Success)?.busy == true
    val actionError = (state as? HerdrUiState.Success)?.actionError
    val closeDialog = {
        dialog = null
        viewModel.clearActionError()
    }
    when (val shown = dialog) {
        is HerdrDialog.Create -> HerdrCreateDialog(
            forWorkspace = shown.workspaceId == null,
            noun = if (shown.server.product == HerdrServer.PRODUCT_CORE) "terminal" else "workspace",
            projects = projects.filter(ProjectInfo::path_exists),
            agents = shown.server.features.agents,
            busy = busy,
            error = actionError,
            onCreate = { values ->
                viewModel.create(
                    HerdrCreateRequest(
                        server_id = shown.server.server_id,
                        placement = if (shown.workspaceId == null) HerdrCreateRequest.PLACEMENT_WORKSPACE
                        else HerdrCreateRequest.PLACEMENT_TAB,
                        workspace_id = shown.workspaceId,
                        cwd = values.cwd,
                        label = values.label,
                        agent = values.agent,
                    ),
                )
            },
            onDismiss = closeDialog,
        )
        is HerdrDialog.Rename -> HerdrRenameDialog(
            target = shown.target,
            current = shown.current,
            busy = busy,
            error = actionError,
            onRename = { viewModel.rename(shown.server.server_id, shown.target, shown.id, it) },
            onDismiss = closeDialog,
        )
        is HerdrDialog.Close -> HerdrCloseDialog(
            target = shown.target,
            label = shown.label,
            busy = busy,
            error = actionError,
            onClose = { viewModel.close(shown.server.server_id, shown.target, shown.id) },
            onDismiss = closeDialog,
        )
        is HerdrDialog.StartAgent -> HerdrStartAgentDialog(
            agents = shown.server.features.agents,
            busy = busy,
            error = actionError,
            onStart = { viewModel.startAgent(shown.server.server_id, shown.paneId, it) },
            onDismiss = closeDialog,
        )
        null -> Unit
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HerdrContent(
    state: HerdrUiState,
    snackbarHostState: SnackbarHostState,
    onBack: () -> Unit,
    onAction: (HerdrAction) -> Unit,
    modifier: Modifier = Modifier,
) {
    PixelScaffold(
        modifier = modifier.testTag(HERDR_SCREEN_TAG),
        topBar = {
            PixelTopBar(
                title = { Text("Herdr") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
        snackbarHost = { PixelSnackbarHost(snackbarHostState) },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            when (state) {
                HerdrUiState.Loading -> LoadingIndicator(modifier = Modifier.testTag(HERDR_LOADING_TAG))
                HerdrUiState.Empty -> PixelEmptyState(
                    text = "No Herdr server runs on this computer. Start Herdr there, and it shows up here.",
                    modifier = Modifier
                        .padding(16.dp)
                        .testTag(HERDR_EMPTY_TAG),
                )
                is HerdrUiState.Success -> HerdrTree(state = state, onAction = onAction)
            }
        }
    }
}

@Composable
private fun HerdrTree(state: HerdrUiState.Success, onAction: (HerdrAction) -> Unit) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item(key = "summary") {
            Text(
                text = state.summary,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (state.agents.isNotEmpty()) {
            item(key = "agents-heading") { SectionHeading("Agents") }
            items(state.agents, key = { "agent-${it.key}" }) { row ->
                HerdrPaneCard(
                    row = row,
                    showWorkspace = true,
                    opening = row.key == state.openingKey,
                    onAction = onAction,
                )
            }
        }
        state.servers.forEach { server ->
            item(key = "server-${server.server_id}") {
                ServerHeading(server = server, servers = state.servers, onAction = onAction)
            }
            if (!server.isReady) {
                item(key = "server-note-${server.server_id}") {
                    PixelEmptyState(server.message ?: "This Herdr server is not ready.")
                }
            } else if (server.workspaces.isEmpty()) {
                item(key = "server-note-${server.server_id}") {
                    PixelEmptyState("Nothing is open in this Herdr server.")
                }
            }
            server.workspaces.forEach { workspace ->
                item(key = "ws-${server.server_id}-${workspace.workspace_id}") {
                    GroupHeading(
                        label = workspace.label,
                        menu = workspaceMenu(server, workspace),
                        onAction = onAction,
                    )
                }
                workspace.tabs.forEach { tab ->
                    // A workspace with one tab reads as a flat list: the tab level adds nothing.
                    if (workspace.tabs.size > 1) {
                        item(key = "tab-${server.server_id}-${tab.tab_id}") {
                            GroupHeading(
                                label = tab.label,
                                menu = tabMenu(server, tab),
                                onAction = onAction,
                                secondary = true,
                            )
                        }
                    }
                    val rows = state.rows.filter {
                        it.server.server_id == server.server_id && it.tab.tab_id == tab.tab_id &&
                            it.workspace.workspace_id == workspace.workspace_id
                    }
                    items(rows, key = { "pane-${it.key}" }) { row ->
                        HerdrPaneCard(
                            row = row,
                            showWorkspace = false,
                            opening = row.key == state.openingKey,
                            onAction = onAction,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun SectionHeading(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        style = MaterialTheme.typography.titleSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = modifier,
    )
}

@Composable
private fun ServerHeading(server: HerdrServer, servers: List<HerdrServer>, onAction: (HerdrAction) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SectionHeading(
            text = listOfNotNull(herdrServerLabel(server, servers), server.version).joinToString(" "),
            modifier = Modifier.weight(1f),
        )
        if (server.isReady && server.features.new_workspace) {
            PixelButton(
                text = if (server.product == HerdrServer.PRODUCT_CORE) "New terminal" else "New workspace",
                onClick = { onAction(HerdrAction.Ask(HerdrDialog.Create(server, workspaceId = null))) },
                compact = true,
            )
        }
    }
}

@Composable
private fun GroupHeading(
    label: String,
    menu: List<Pair<String, HerdrAction>>,
    onAction: (HerdrAction) -> Unit,
    secondary: Boolean = false,
) {
    Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(
            text = label,
            style = if (secondary) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.titleMedium,
            color = if (secondary) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        ActionMenu(description = "Actions for $label", items = menu, onAction = onAction)
    }
}

@Composable
private fun ActionMenu(
    description: String,
    items: List<Pair<String, HerdrAction>>,
    onAction: (HerdrAction) -> Unit,
) {
    if (items.isEmpty()) return
    var expanded by remember { mutableStateOf(false) }
    Box {
        PixelIconButton(Icons.Default.MoreVert, description, onClick = { expanded = true }, compact = true)
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            items.forEach { (text, action) ->
                DropdownMenuItem(
                    text = { Text(text) },
                    onClick = {
                        expanded = false
                        onAction(action)
                    },
                )
            }
        }
    }
}

@Composable
private fun HerdrPaneCard(
    row: HerdrPaneRow,
    showWorkspace: Boolean,
    /** The pane is being opened: the open is a round trip to the agent. */
    opening: Boolean,
    onAction: (HerdrAction) -> Unit,
) {
    val pane = row.pane
    PixelCard(
        modifier = Modifier
            .fillMaxWidth()
            .testTag(HERDR_PANE_ROW_TAG),
        onClick = { onAction(HerdrAction.Open(row)) },
    ) {
        Row(
            modifier = Modifier.padding(start = 16.dp, top = 12.dp, bottom = 12.dp, end = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            HerdrStatusMark(row.status)
            Spacer(modifier = Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = herdrPaneTitle(pane),
                    style = MaterialTheme.typography.titleSmall,
                    fontFamily = ChromeFontFamily,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = listOfNotNull(
                        pane.agent?.let(::herdrAgentLabel) ?: "Terminal",
                        row.workspace.label.takeIf { showWorkspace && it.isNotBlank() },
                    ).joinToString(" · "),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                pane.cwd?.let { cwd ->
                    Text(
                        text = cwd,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            if (opening) {
                Spacer(modifier = Modifier.width(8.dp))
                PixelBlinkCursor(height = 16.dp, modifier = Modifier.testTag(HERDR_PANE_OPENING_TAG))
            } else {
                herdrChip(row.status)?.let { chip ->
                    Spacer(modifier = Modifier.width(8.dp))
                    SessionAttentionChip(chip)
                }
            }
            ActionMenu(
                description = "Actions for ${herdrPaneTitle(pane)}",
                items = paneMenu(row),
                onAction = onAction,
            )
        }
    }
}

private fun paneMenu(row: HerdrPaneRow): List<Pair<String, HerdrAction>> = buildList {
    val server = row.server
    val pane = row.pane
    val features: HerdrFeatures = server.features
    if (row.status == HerdrStatus.Done && pane.turn != null) add("Mark as seen" to HerdrAction.Seen(row))
    if (features.split) {
        add("Split right" to HerdrAction.Split(row, HerdrCreateRequest.PLACEMENT_SPLIT_RIGHT))
        add("Split down" to HerdrAction.Split(row, HerdrCreateRequest.PLACEMENT_SPLIT_DOWN))
    }
    if (pane.agent == null && features.agents.isNotEmpty()) {
        add("Start an agent…" to HerdrAction.Ask(HerdrDialog.StartAgent(server, pane.pane_id)))
    }
    if (features.rename) {
        add(
            "Rename…" to HerdrAction.Ask(
                HerdrDialog.Rename(server, HerdrTarget.PANE, pane.pane_id, herdrPaneTitle(pane)),
            ),
        )
    }
    if (features.close_pane) {
        add(
            "Close pane…" to HerdrAction.Ask(
                HerdrDialog.Close(server, HerdrTarget.PANE, pane.pane_id, herdrPaneTitle(pane)),
            ),
        )
    }
}

private fun workspaceMenu(server: HerdrServer, workspace: HerdrWorkspace): List<Pair<String, HerdrAction>> =
    buildList {
        val id = workspace.workspace_id
        if (server.features.new_tab) add("New tab…" to HerdrAction.Ask(HerdrDialog.Create(server, id)))
        if (server.features.rename) {
            add("Rename…" to HerdrAction.Ask(HerdrDialog.Rename(server, HerdrTarget.WORKSPACE, id, workspace.label)))
        }
        if (server.features.close_group) {
            add(
                "Close workspace…" to
                    HerdrAction.Ask(HerdrDialog.Close(server, HerdrTarget.WORKSPACE, id, workspace.label)),
            )
        }
    }

private fun tabMenu(server: HerdrServer, tab: HerdrTab): List<Pair<String, HerdrAction>> = buildList {
    if (server.features.rename) {
        add("Rename…" to HerdrAction.Ask(HerdrDialog.Rename(server, HerdrTarget.TAB, tab.tab_id, tab.label)))
    }
    if (server.features.close_group) {
        add("Close tab…" to HerdrAction.Ask(HerdrDialog.Close(server, HerdrTarget.TAB, tab.tab_id, tab.label)))
    }
}

private fun previewState(): HerdrUiState.Success {
    val server = HerdrServer(
        server_id = "tui:default",
        version = "0.9.5",
        features = HerdrFeatures(new_workspace = true, new_tab = true, split = true, rename = true),
        workspaces = listOf(
            HerdrWorkspace(
                workspace_id = "w1",
                label = "mobaide",
                tabs = listOf(
                    HerdrTab(
                        tab_id = "w1:t1",
                        label = "main",
                        panes = listOf(
                            HerdrPane("w1:p1", "api refactor", agent = "claude", status = "blocked", cwd = "/home/me/mobaide"),
                            HerdrPane("w1:p2", "tests", agent = "codex", status = "working", cwd = "/home/me/mobaide"),
                            HerdrPane("w1:p3", "shell", cwd = "/home/me/mobaide"),
                        ),
                    ),
                ),
            ),
        ),
    )
    val rows = herdrPaneRows("agent", com.mobaide.app.data.network.dto.HerdrSnapshot(listOf(server)), emptyMap())
    return HerdrUiState.Success(listOf(server), rows, herdrAgentRows(rows), herdrSummary(rows))
}

@Preview(name = "Herdr dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun HerdrScreenPreviewDark() {
    MobaideTheme(darkTheme = true) {
        HerdrContent(previewState(), remember { SnackbarHostState() }, onBack = {}, onAction = {})
    }
}

@Preview(name = "Herdr light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun HerdrScreenPreviewLight() {
    MobaideTheme(darkTheme = false) {
        HerdrContent(previewState(), remember { SnackbarHostState() }, onBack = {}, onAction = {})
    }
}
