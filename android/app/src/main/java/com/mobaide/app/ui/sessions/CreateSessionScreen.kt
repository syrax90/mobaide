package com.mobaide.app.ui.sessions

import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.height
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.selected
import sh.calvin.reorderable.ReorderableItem
import sh.calvin.reorderable.rememberReorderableLazyListState
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.exclude
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.outlined.Login
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Radar
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material.icons.outlined.History
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import androidx.compose.foundation.BorderStroke
import androidx.compose.material3.Card
import androidx.compose.material3.CardColors
import androidx.compose.material3.CardDefaults
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.InputChip
import androidx.compose.material3.InputChipDefaults
import androidx.compose.material3.ListItem
import androidx.compose.material3.ListItemColors
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.theme.pixel
import androidx.compose.material3.OutlinedTextField
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import com.mobaide.app.ui.common.pixel.PixelScaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.ui.graphics.RectangleShape
import com.mobaide.app.ui.common.pixel.pixelSegmentedButtonColors
import com.mobaide.app.ui.common.pixel.pixelSwitchColors
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import com.mobaide.app.ui.common.pixel.PixelSnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.PixelTopBar
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.selection.toggleable
import com.mobaide.app.ui.theme.ChromeFontFamily
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.AUTO_SCAN_UI_CHAT
import com.mobaide.app.data.network.dto.ChatSupport
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.ui.common.pathBrowseHint
import com.mobaide.app.ui.common.DragHandleIcon
import com.mobaide.app.ui.common.PathSuggestionsUi
import com.mobaide.app.ui.common.PathTextField
import com.mobaide.app.ui.common.SecretTextField
import com.mobaide.app.ui.workspaces.WorkspaceFormSheet
import com.mobaide.app.ui.workspaces.WorkspacesUiEvent
import com.mobaide.app.ui.workspaces.WorkspacesViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CreateSessionScreen(
    viewModel: CreateSessionViewModel,
    workspacesViewModel: WorkspacesViewModel,
    onBack: () -> Unit,
    onSessionCreated: (String) -> Unit,
    onAutoScanStarted: (String) -> Unit,
    // The GitHub setup terminal: opened on top of the wizard so Back returns to it.
    onSetupSessionOpened: (String) -> Unit = onSessionCreated,
    chatResumeViewModel: ChatResumeViewModel = hiltViewModel(),
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val resumeState by chatResumeViewModel.uiState.collectAsStateWithLifecycle()
    var showResume by rememberSaveable { mutableStateOf(false) }
    // "Continue a session" of one agent ([kind]): its stored conversations in this folder.
    val openResume = { kind: String, chat: ChatSupport? ->
        chatResumeViewModel.open(kind, state.selectedFolder, (chat as? ChatSupport.Available)?.live == true)
        showResume = true
    }
    val workspacesState by workspacesViewModel.uiState.collectAsStateWithLifecycle()
    val customPathSuggestions by viewModel.customPathSuggestionState.collectAsStateWithLifecycle()
    val workspacePathSuggestions by workspacesViewModel.pathSuggestionState.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(viewModel) {
        viewModel.navigateToSession.collect { sessionId -> onSessionCreated(sessionId) }
    }
    LaunchedEffect(viewModel) {
        viewModel.navigateToAutoScan.collect { workspaceName -> onAutoScanStarted(workspaceName) }
    }
    LaunchedEffect(viewModel) {
        viewModel.navigateToSetupSession.collect { sessionId -> onSetupSessionOpened(sessionId) }
    }
    LaunchedEffect(viewModel) {
        viewModel.transientMessages.collect { msg -> snackbar.showSnackbar(msg) }
    }
    LaunchedEffect(workspacesViewModel) {
        workspacesViewModel.workspaceCreated.collect { newWorkspace ->
            viewModel.onWorkspaceCreated(newWorkspace)
        }
    }
    LaunchedEffect(workspacesViewModel) {
        // Only Message events apply to the create-only flow on this screen.
        // DeletedWithUndo / LinearKeyReminder / CloseSheet belong to WorkspacesScreen.
        workspacesViewModel.events.collect { event ->
            if (event is WorkspacesUiEvent.Message) snackbar.showSnackbar(event.text)
        }
    }
    LaunchedEffect(state.error) {
        state.error?.let {
            snackbar.showSnackbar(it)
            viewModel.clearError()
        }
    }
    LaunchedEffect(state.dismiss) {
        if (state.dismiss) {
            viewModel.consumeDismiss()
            onBack()
        }
    }
    // Re-probe CLI availability every time the user reaches the mode picker so
    // the Claude / Codex / Auto Scan cards reflect what's installed on the agent
    // right now. The ViewModel survives intra-wizard step changes, so a one-shot
    // init fetch would go stale on a return visit to this step.
    LaunchedEffect(state.step) {
        if (state.step == CreateSessionStep.ModePick) viewModel.refreshCliAvailability()
    }

    // System back navigates between steps before exiting the screen.
    BackHandler(enabled = state.step != CreateSessionStep.WorkspaceSelect) {
        viewModel.goBack()
    }

    val onTopBarBack: () -> Unit = {
        if (state.step == CreateSessionStep.WorkspaceSelect) onBack() else viewModel.goBack()
    }

    PixelScaffold(
        topBar = {
            PixelTopBar(
                title = {
                    Text(
                        when (state.step) {
                            CreateSessionStep.WorkspaceSelect -> "New Session"
                            CreateSessionStep.ModePick -> "Select mode"
                            CreateSessionStep.StandardConfig -> "Claude code session"
                            CreateSessionStep.CodexConfig -> "Codex session"
                            CreateSessionStep.AntigravityConfig -> "Antigravity session"
                            CreateSessionStep.OpenCodeConfig -> "OpenCode session"
                            CreateSessionStep.CopilotConfig -> "Copilot session"
                            CreateSessionStep.CursorConfig -> "Cursor session"
                            CreateSessionStep.JunieConfig -> "Junie session"
                            CreateSessionStep.PiConfig -> "Pi session"
                            CreateSessionStep.AutoScanConfig -> "Auto Scan"
                            // TerminalConfig is shared between Terminal mode and
                            // custom agent types — title by the selected mode.
                            CreateSessionStep.TerminalConfig ->
                                (state.selectedMode as? CreateSessionMode.Custom)
                                    ?.let { mode ->
                                        state.customTypes
                                            .firstOrNull { it.id == mode.typeId }?.name
                                    }
                                    ?: "Terminal session"
                        },
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onTopBarBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
        snackbarHost = { PixelSnackbarHost(snackbar) },
    ) { padding ->
        // The app is edge-to-edge, so adjustResize never shrinks the window: the
        // keyboard arrives only as an inset. Consume it above the scroll container so
        // the viewport shrinks and the focused field scrolls clear of the keyboard.
        // Scaffold already inset this content by systemBars — add only the excess.
        val bodyModifier = Modifier
            .fillMaxSize()
            .padding(padding)
            .windowInsetsPadding(WindowInsets.ime.exclude(WindowInsets.systemBars))
        run {
            val storedWorkspace = (state.selection as? WorkspaceSelection.Stored)?.let { sel ->
                state.workspaces.firstOrNull { it.name == sel.name }
            }
            val customPathSelected = state.selection as? WorkspaceSelection.CustomPath
            val modeHeader = when (val sel = state.selection) {
                is WorkspaceSelection.Stored -> ModeHeader(
                    title = storedWorkspace?.name ?: sel.name,
                    subtitle = storedWorkspace?.path,
                )
                is WorkspaceSelection.CustomPath -> ModeHeader(
                    title = "Custom path",
                    subtitle = sel.path,
                )
                WorkspaceSelection.None -> ModeHeader(
                    title = "",
                    subtitle = null,
                )
            }

            when (state.step) {
                CreateSessionStep.WorkspaceSelect -> WorkspaceSelectStep(
                    state = state,
                    customPathSelected = customPathSelected != null,
                    modifier = bodyModifier,
                    onSelect = viewModel::selectWorkspace,
                    onReorder = viewModel::reorderWorkspaces,
                    onMove = viewModel::moveWorkspace,
                    onCustomPathSelected = viewModel::selectCustomPath,
                    onCustomPathChange = viewModel::setCustomPathDraft,
                    onPickRecentCustomPath = viewModel::pickRecentCustomPath,
                    onRemoveRecentCustomPath = viewModel::removeRecentCustomPath,
                    customPathSuggestions = customPathSuggestions,
                    onCustomPathFocusChange = viewModel::onCustomPathFocusChange,
                    onPickCustomPathSuggestion = viewModel::pickCustomPathSuggestion,
                    onCreateWorkspaceClicked = workspacesViewModel::openCreateSheet,
                    onContinue = viewModel::confirmWorkspace,
                )
                CreateSessionStep.ModePick -> ModePickerStep(
                    header = modeHeader,
                    state = state,
                    modifier = bodyModifier,
                    onChooseMode = viewModel::chooseMode,
                    onAddCustomType = { viewModel.openTypeEditor(null) },
                    onEditCustomType = viewModel::openTypeEditor,
                    onDeleteCustomType = viewModel::requestDeleteType,
                    onReorder = viewModel::reorderModes,
                    onMove = viewModel::moveMode,
                )
                else -> Column(
                    modifier = bodyModifier
                        .padding(16.dp)
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    when (state.step) {
                        CreateSessionStep.StandardConfig -> StandardConfigSection(
                            state = state,
                            showWorktreeToggle = state.selection is WorkspaceSelection.Stored &&
                                storedWorkspace?.is_git == true,
                            viewModel = viewModel,
                            onContinueSession = { openResume("claude", state.claudeChat) },
                        )
                        CreateSessionStep.CodexConfig -> CodexConfigSection(
                            state = state,
                            showWorktreeToggle = state.selection is WorkspaceSelection.Stored &&
                                storedWorkspace?.is_git == true,
                            viewModel = viewModel,
                            onContinueSession = { openResume("codex", state.codexChat) },
                        )
                        CreateSessionStep.AntigravityConfig -> AntigravityConfigSection(
                            state = state,
                            showWorktreeToggle = state.selection is WorkspaceSelection.Stored &&
                                storedWorkspace?.is_git == true,
                            viewModel = viewModel,
                            onContinueSession = { openResume("antigravity", state.antigravityChat) },
                        )
                        CreateSessionStep.OpenCodeConfig -> OpenCodeConfigSection(
                            state = state,
                            showWorktreeToggle = state.selection is WorkspaceSelection.Stored &&
                                storedWorkspace?.is_git == true,
                            viewModel = viewModel,
                            onContinueSession = { openResume("opencode", state.opencodeChat) },
                        )
                        CreateSessionStep.CopilotConfig -> CopilotConfigSection(
                            state = state,
                            showWorktreeToggle = state.selection is WorkspaceSelection.Stored &&
                                storedWorkspace?.is_git == true,
                            viewModel = viewModel,
                            onContinueSession = { openResume("copilot", state.copilotChat) },
                        )
                        CreateSessionStep.CursorConfig -> CursorConfigSection(
                            state = state,
                            showWorktreeToggle = state.selection is WorkspaceSelection.Stored &&
                                storedWorkspace?.is_git == true,
                            viewModel = viewModel,
                            onContinueSession = { openResume("cursor", state.cursorChat) },
                        )
                        CreateSessionStep.JunieConfig -> JunieConfigSection(
                            state = state,
                            showWorktreeToggle = state.selection is WorkspaceSelection.Stored &&
                                storedWorkspace?.is_git == true,
                            viewModel = viewModel,
                            onContinueSession = { openResume("junie", state.junieChat) },
                        )
                        CreateSessionStep.PiConfig -> PiConfigSection(
                            state = state,
                            showWorktreeToggle = state.selection is WorkspaceSelection.Stored &&
                                storedWorkspace?.is_git == true,
                            viewModel = viewModel,
                            onContinueSession = { openResume("pi", state.piChat) },
                        )
                        CreateSessionStep.AutoScanConfig -> AutoScanConfigSection(
                            state = state,
                            viewModel = viewModel,
                        )
                        CreateSessionStep.TerminalConfig -> TerminalConfigSection(
                            state = state,
                            viewModel = viewModel,
                        )
                        CreateSessionStep.WorkspaceSelect, CreateSessionStep.ModePick -> Unit
                    }
                }
            }
        }
    }

    val resumeKind = when (state.step) {
        CreateSessionStep.StandardConfig -> "claude"
        CreateSessionStep.CodexConfig -> "codex"
        CreateSessionStep.AntigravityConfig -> "antigravity"
        CreateSessionStep.OpenCodeConfig -> "opencode"
        CreateSessionStep.CopilotConfig -> "copilot"
        CreateSessionStep.CursorConfig -> "cursor"
        CreateSessionStep.JunieConfig -> "junie"
        CreateSessionStep.PiConfig -> "pi"
        else -> null
    }
    if (showResume && resumeKind != null) {
        val resumeChat = when (resumeKind) {
            "codex" -> state.codexChat
            "antigravity" -> state.antigravityChat
            "opencode" -> state.opencodeChat
            "copilot" -> state.copilotChat
            "cursor" -> state.cursorChat
            "junie" -> state.junieChat
            "pi" -> state.piChat
            else -> state.claudeChat
        }
        LaunchedEffect(resumeKind) {
            chatResumeViewModel.ensureOpen(
                resumeKind,
                state.selectedFolder,
                (resumeChat as? ChatSupport.Available)?.live == true,
            )
        }
        ChatResumeSheet(
            state = resumeState,
            onSetAllFolders = chatResumeViewModel::setAllFolders,
            onRetry = chatResumeViewModel::retry,
            onLoadMore = chatResumeViewModel::loadMore,
            onPick = { row ->
                showResume = false
                val openId = row.open_session_id
                if (openId != null) onSessionCreated(openId) else viewModel.resumeChatSession(resumeKind, row)
            },
            onDismiss = { showResume = false },
        )
    }

    val form = workspacesState.form
    if (form != null) {
        WorkspaceFormSheet(
            state = form,
            onDismiss = workspacesViewModel::closeSheet,
            onNameChange = workspacesViewModel::onNameChange,
            onPathChange = workspacesViewModel::onPathChange,
            onCondaChange = workspacesViewModel::onCondaChange,
            onLinearKeyChange = workspacesViewModel::onLinearKeyChange,
            onToggleKeyVisible = workspacesViewModel::toggleLinearKeyVisible,
            onRequestClearLinearKey = workspacesViewModel::requestClearLinearKey,
            onTouch = workspacesViewModel::touch,
            onAddFlag = workspacesViewModel::addFlag,
            onUpdateFlag = workspacesViewModel::updateFlag,
            onRemoveFlag = workspacesViewModel::removeFlag,
            onToggleTriggerLabel = workspacesViewModel::toggleTriggerLabel,
            onToggleTriggerState = workspacesViewModel::toggleTriggerState,
            onActiveStateChange = workspacesViewModel::onActiveStateChange,
            onDoneStateChange = workspacesViewModel::onDoneStateChange,
            onRemovalLabelChange = workspacesViewModel::onRemovalLabelChange,
            onRefreshFilterMetadata = workspacesViewModel::refreshFilterMetadata,
            onSave = workspacesViewModel::save,
            pathSuggestions = workspacePathSuggestions,
            onPathFocusChange = workspacesViewModel::onPathFocusChange,
            onPickPathSuggestion = workspacesViewModel::pickPathSuggestion,
        )
    }

    if (state.codexLoginVisible) {
        CodexLoginDialog(
            apiKey = state.codexApiKeyDraft,
            revealed = state.codexApiKeyVisible,
            submitting = state.codexLoginSubmitting,
            error = state.codexLoginError,
            onKeyChange = viewModel::setCodexApiKeyDraft,
            onToggleRevealed = viewModel::toggleCodexApiKeyVisible,
            onSubmit = viewModel::submitCodexLogin,
            onDismiss = viewModel::dismissCodexLogin,
        )
    }

    val typeEditor = state.typeEditor
    if (typeEditor is TypeEditorState.Open) {
        CustomAgentTypeEditorSheet(
            target = typeEditor.target,
            error = state.typeEditorError,
            saving = state.typeEditorSaving,
            onSave = viewModel::saveCustomAgentType,
            onDismiss = viewModel::dismissTypeEditor,
        )
    }

    state.deleteTypeTarget?.let { target ->
        DeleteCustomAgentTypeDialog(
            name = target.name,
            deleting = state.deleteTypeInFlight,
            onConfirm = viewModel::confirmDeleteType,
            onDismiss = viewModel::dismissDeleteType,
        )
    }
}

/**
 * Delete confirmation for a custom agent type. The wording covers the server's
 * shortcut cascade so the destructive scope is explicit before confirming.
 */
@Composable
private fun DeleteCustomAgentTypeDialog(
    name: String,
    deleting: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    PixelAlertDialog(
        // Ignore back-press / outside-tap while the delete is in flight,
        // matching the disabled Cancel button.
        onDismissRequest = { if (!deleting) onDismiss() },
        title = { Text("Delete agent type?") },
        text = {
            Text(
                "Delete \"$name\"? Saved shortcuts for this agent type will " +
                    "also be deleted. Running sessions are not affected.",
            )
        },
        confirmButton = {
            PixelTextButton(
                onClick = onConfirm,
                enabled = !deleting,
                modifier = Modifier.testTag("delete_custom_type_confirm"),
            ) {
                Text(
                    if (deleting) "Deleting…" else "Delete",
                    color = MaterialTheme.colorScheme.error,
                )
            }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss, enabled = !deleting) { Text("Cancel") }
        },
    )
}

/**
 * Sign-in prompt shown when creating a Codex session fails because the agent's
 * Codex CLI isn't authenticated. The key is forwarded to `POST /api/codex/login`
 * and held only in transient state — never persisted on the device. On success
 * the ViewModel retries the original create.
 */
@Composable
private fun CodexLoginDialog(
    apiKey: String,
    revealed: Boolean,
    submitting: Boolean,
    error: String?,
    onKeyChange: (String) -> Unit,
    onToggleRevealed: () -> Unit,
    onSubmit: () -> Unit,
    onDismiss: () -> Unit,
) {
    PixelAlertDialog(
        // Ignore back-press / outside-tap while a login is in flight, matching
        // the disabled Cancel button — otherwise dismissing mid-login leaves the
        // coroutine running and it would still retry the session create on success.
        onDismissRequest = { if (!submitting) onDismiss() },
        title = { Text("Codex needs sign-in") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    "Codex isn't signed in on this agent. Enter an OpenAI API key to " +
                        "sign in — it's sent to the agent and never stored on your device.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                SecretTextField(
                    value = apiKey,
                    onValueChange = onKeyChange,
                    label = "OpenAI API key",
                    revealed = revealed,
                    onToggleRevealed = onToggleRevealed,
                    enabled = !submitting,
                    modifier = Modifier.fillMaxWidth(),
                )
                if (error != null) {
                    Text(
                        text = error,
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        },
        confirmButton = {
            PixelButton(
                onClick = onSubmit,
                style = PixelButtonStyle.Primary,
                enabled = apiKey.isNotBlank() && !submitting,
            ) {
                Text(if (submitting) "Signing in…" else "Sign in")
            }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss, enabled = !submitting) {
                Text("Cancel")
            }
        },
    )
}

private const val WORKSPACE_KEY = "workspace:"
private const val MODE_KEY = "mode:"
private val HANDLE_WIDTH = 48.dp
private const val REORDER_HINT = "Drag a handle to reorder — the order syncs to all your devices."

/** [this] with the item keyed [fromKey] moved to where [toKey] is. */
private fun <T> List<T>.movedByKey(fromKey: Any?, toKey: Any?, keyOf: (T) -> String): List<T> {
    val from = indexOfFirst { keyOf(it) == fromKey }
    val to = indexOfFirst { keyOf(it) == toKey }
    if (from < 0 || to < 0 || from == to) return this
    return toMutableList().apply { add(to, removeAt(from)) }
}

/**
 * TalkBack's way to reorder: "Move up" / "Move down" actions on the row's card,
 * since a screen-reader user cannot drag the handle.
 */
private fun Modifier.moveActions(index: Int, count: Int, onMove: (Int, Int) -> Unit): Modifier =
    if (count < 2) this else semantics {
        customActions = buildList {
            if (index > 0) {
                add(CustomAccessibilityAction("Move up") { onMove(index, index - 1); true })
            }
            if (index < count - 1) {
                add(CustomAccessibilityAction("Move down") { onMove(index, index + 1); true })
            }
        }
    }

/**
 * A wizard row: the drag handle in a gutter left of the card.
 * A row that cannot move keeps the handle's width empty when [reserveHandle],
 * so all cards in the list line up.
 */
@Composable
private fun HandleRow(
    handle: (@Composable () -> Unit)?,
    reserveHandle: Boolean,
    content: @Composable () -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        when {
            handle != null -> handle()
            reserveHandle -> Spacer(Modifier.width(HANDLE_WIDTH))
        }
        Box(modifier = Modifier.weight(1f)) { content() }
    }
}

/**
 * The grip a row is dragged by. Outside the card on purpose: a tap here hits
 * nothing, so a press that never turns into a drag cannot pick the card.
 */
@Composable
private fun WizardDragHandle(label: String, dragging: Boolean, modifier: Modifier) {
    Box(
        modifier = modifier
            .width(HANDLE_WIDTH)
            .height(48.dp),
        contentAlignment = Alignment.Center,
    ) {
        DragHandleIcon(
            contentDescription = "Reorder $label",
            tint = if (dragging) MaterialTheme.pixel.brand2 else MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/**
 * Workspace step. Each stored workspace is a card: tap selects it, the handle drags
 * it. The order saved is the agent's own workspace order, so every client shows
 * it. "Custom path…" and "Create new workspace…" stay last and do not move.
 *
 * A LazyColumn rather than the config steps' scrolling Column, so a drag near
 * the top or bottom edge scrolls the list.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WorkspaceSelectStep(
    state: CreateSessionUiState,
    customPathSelected: Boolean,
    modifier: Modifier,
    onSelect: (String) -> Unit,
    onReorder: (List<String>) -> Unit,
    onMove: (Int, Int) -> Unit,
    onCustomPathSelected: () -> Unit,
    onCustomPathChange: (String) -> Unit,
    onPickRecentCustomPath: (String) -> Unit,
    onRemoveRecentCustomPath: (String) -> Unit,
    customPathSuggestions: PathSuggestionsUi,
    onCustomPathFocusChange: (Boolean) -> Unit,
    onPickCustomPathSuggestion: (String) -> Unit,
    onCreateWorkspaceClicked: () -> Unit,
    onContinue: () -> Unit,
) {
    val listState = rememberLazyListState()
    val haptic = LocalHapticFeedback.current
    // Moved in the drag's own frame, so the library sees each swap at once. The
    // view model's order replaces it on every upstream change — except during a
    // drag, where the answer to the previous drop would undo the swaps so far.
    var display by remember { mutableStateOf(state.workspaces) }
    val reorderState = rememberReorderableLazyListState(listState) { from, to ->
        display = display.movedByKey(from.key, to.key) { WORKSPACE_KEY + it.name }
        haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove)
    }
    LaunchedEffect(state.workspaces, reorderState.isAnyItemDragging) {
        if (!reorderState.isAnyItemDragging) display = state.workspaces
    }
    val reorderable = display.size > 1
    val selectedName = (state.selection as? WorkspaceSelection.Stored)?.name
    val canContinue = when (val sel = state.selection) {
        is WorkspaceSelection.Stored -> true
        is WorkspaceSelection.CustomPath ->
            state.customPathDraft.isNotBlank() && state.customPathError == null
        WorkspaceSelection.None -> false
    }

    LazyColumn(
        state = listState,
        modifier = modifier,
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item(key = "workspace_header") {
            WizardListHeader(
                title = "Workspace",
                hint = if (reorderable) REORDER_HINT else null,
            )
        }
        if (display.isEmpty()) {
            item(key = "workspace_empty") {
                Text(
                    text = if (state.isLoadingWorkspaces) {
                        "Loading workspaces…"
                    } else {
                        "This agent has no workspaces yet. Create one, or run once in a custom path."
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        itemsIndexed(display, key = { _, workspace -> WORKSPACE_KEY + workspace.name }) { index, workspace ->
            ReorderableItem(reorderState, key = WORKSPACE_KEY + workspace.name) { isDragging ->
                val handleModifier = Modifier.draggableHandle(
                    onDragStarted = { haptic.performHapticFeedback(HapticFeedbackType.LongPress) },
                    onDragStopped = { onReorder(display.map { it.name }) },
                )
                HandleRow(
                    handle = if (reorderable) {
                        { WizardDragHandle(workspace.name, isDragging, handleModifier) }
                    } else {
                        null
                    },
                    reserveHandle = false,
                ) {
                    WorkspacePickCard(
                        workspace = workspace,
                        selected = workspace.name == selectedName,
                        dragging = isDragging,
                        onClick = { onSelect(workspace.name) },
                        modifier = Modifier.moveActions(index, display.size, onMove),
                    )
                }
            }
        }
        item(key = "custom_path_row") {
            HandleRow(handle = null, reserveHandle = reorderable) {
                WizardActionCard(
                    title = "Custom path…",
                    subtitle = "Run once — no workspace is created",
                    icon = Icons.Default.FolderOpen,
                    selected = customPathSelected,
                    onClick = onCustomPathSelected,
                    contentDescription = "Custom path",
                    testTag = "workspace_custom_path",
                )
            }
        }
        item(key = "create_workspace_row") {
            HandleRow(handle = null, reserveHandle = reorderable) {
                WizardActionCard(
                    title = "Create new workspace…",
                    subtitle = "Name + path, saved on this agent for next time",
                    icon = Icons.Default.Add,
                    selected = false,
                    onClick = onCreateWorkspaceClicked,
                    contentDescription = "Create new workspace",
                    testTag = "workspace_create",
                )
            }
        }
        if (customPathSelected) {
            item(key = "custom_path_panel") {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    CustomPathPanel(
                        state = state,
                        onDraftChange = onCustomPathChange,
                        onPickRecent = onPickRecentCustomPath,
                        onRemoveRecent = onRemoveRecentCustomPath,
                        suggestions = customPathSuggestions,
                        onFocusChange = onCustomPathFocusChange,
                        onPickSuggestion = onPickCustomPathSuggestion,
                    )
                }
            }
        }
        item(key = "workspace_continue") {
            PixelButton(
                style = PixelButtonStyle.Primary,
                onClick = onContinue,
                enabled = canContinue,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Continue")
            }
        }
    }
}

/** A list's title, with an optional one-line hint under it. */
@Composable
private fun WizardListHeader(title: String, hint: String?) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(text = title, style = MaterialTheme.typography.titleMedium)
        if (hint != null) {
            Text(
                text = hint,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private val selectedCardColors: CardColors
    @Composable get() = CardDefaults.cardColors(
        containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
    )

private val transparentListColors: ListItemColors
    @Composable get() = ListItemDefaults.colors(containerColor = Color.Transparent)

/** A stored workspace on the workspace step. */
@Composable
private fun WorkspacePickCard(
    workspace: com.mobaide.app.data.network.dto.WorkspaceInfo,
    selected: Boolean,
    dragging: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(
        onClick = onClick,
        colors = if (selected || dragging) selectedCardColors else CardDefaults.cardColors(),
        border = BorderStroke(
            2.dp,
            if (selected || dragging) MaterialTheme.pixel.brand2 else MaterialTheme.pixel.surface2,
        ),
        modifier = modifier
            .fillMaxWidth()
            .semantics { this.selected = selected }
            .testTag("workspace_row_${workspace.name}"),
    ) {
        ListItem(
            headlineContent = {
                Text(workspace.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
            },
            supportingContent = {
                Column {
                    Text(workspace.path, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    if (!workspace.path_exists) {
                        Text(
                            text = "Folder not found on the agent",
                            color = MaterialTheme.pixel.warning,
                        )
                    }
                    if (workspace.has_linear_key) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            Icon(
                                imageVector = Icons.Filled.Check,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.size(16.dp),
                            )
                            Text("Linear key configured")
                        }
                    }
                }
            },
            colors = transparentListColors,
        )
    }
}

/** A fixed row under a reorderable list ("Custom path…", "Create new workspace…"). */
@Composable
private fun WizardActionCard(
    title: String,
    subtitle: String,
    icon: ImageVector,
    selected: Boolean,
    onClick: () -> Unit,
    contentDescription: String,
    testTag: String,
    enabled: Boolean = true,
) {
    Card(
        onClick = onClick,
        enabled = enabled,
        colors = if (selected) selectedCardColors else CardDefaults.cardColors(),
        border = BorderStroke(
            2.dp,
            if (selected) MaterialTheme.pixel.brand2 else MaterialTheme.pixel.surface2,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .semantics {
                this.contentDescription = contentDescription
                this.selected = selected
            }
            .testTag(testTag),
    ) {
        ListItem(
            headlineContent = { Text(title) },
            supportingContent = { Text(subtitle) },
            leadingContent = { Icon(icon, contentDescription = null) },
            colors = transparentListColors,
        )
    }
}

// Internal, not private, so the chip's pick-vs-remove split is click-testable
// directly (ShortcutPickerContent is exposed for the same reason).
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun CustomPathPanel(
    state: CreateSessionUiState,
    onDraftChange: (String) -> Unit,
    onPickRecent: (String) -> Unit,
    onRemoveRecent: (String) -> Unit,
    suggestions: PathSuggestionsUi = PathSuggestionsUi.None,
    onFocusChange: (Boolean) -> Unit = {},
    onPickSuggestion: (String) -> Unit = {},
) {
    PathTextField(
        value = state.customPathDraft,
        onValueChange = onDraftChange,
        suggestions = suggestions,
        onFocusChange = onFocusChange,
        onPickSuggestion = onPickSuggestion,
        label = { Text("Absolute path on the agent") },
        isError = state.customPathError != null,
        supportingText = {
            val example = if (suggestions.windowsPaths) "e.g. D:\\workspaces\\demo" else "e.g. /home/user/code"
            Text(text = state.customPathError ?: "$example — ${pathBrowseHint(suggestions.windowsPaths)}")
        },
    )
    if (state.recentCustomPaths.isNotEmpty()) {
        Text(
            text = "Recent paths",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        FlowRow(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            state.recentCustomPaths.forEach { path ->
                InputChip(
                    selected = false,
                    onClick = { onPickRecent(path) },
                    label = { Text(path) },
                    trailingIcon = {
                        // The ✕ needs real 48dp layout bounds: an ancestor's
                        // in-bounds hit does not suppress a descendant's near hit
                        // — so taps meant to PICK would silently REMOVE instead.
                        Box(
                            contentAlignment = Alignment.Center,
                            modifier = Modifier
                                .minimumInteractiveComponentSize()
                                .clickable(role = Role.Button) { onRemoveRecent(path) },
                        ) {
                            Icon(
                                imageVector = Icons.Filled.Close,
                                contentDescription = "Remove $path from recent paths",
                                modifier = Modifier.size(InputChipDefaults.IconSize),
                            )
                        }
                    },
                    modifier = Modifier.testTag("recent_custom_path_chip"),
                )
            }
        }
    }
}

/** Display metadata for the mode picker — derived once at the call site
 *  so the section composable doesn't have to know about the sealed
 *  [WorkspaceSelection]. */
private data class ModeHeader(
    val title: String,
    val subtitle: String?,
)

/**
 * Mode picker. There is no confirm button: a card tap commits the choice and
 * moves the wizard on (`onChooseMode`), so each card carries a trailing chevron
 * to read as navigation. The selected highlight survives for the return trip
 * via Back.
 *
 * Built-in and custom cards share one list in the account's saved order; the
 * handle drags a card, and "Add custom agent…" stays last.
 */
@Composable
private fun ModePickerStep(
    header: ModeHeader,
    state: CreateSessionUiState,
    modifier: Modifier,
    onChooseMode: (CreateSessionMode) -> Unit,
    onAddCustomType: () -> Unit,
    onEditCustomType: (CustomAgentTypeDto) -> Unit,
    onDeleteCustomType: (CustomAgentTypeDto) -> Unit,
    onReorder: (List<String>) -> Unit,
    onMove: (Int, Int) -> Unit,
) {
    val submitting = state.submitting
    // Transparent container so the Card (incl. its dimmed disabled colour)
    // shows through; the disabled variant also fades the text to the
    // Material disabled-content alpha so a grayed-out card reads as inert.
    val disabledList = ListItemDefaults.colors(
        containerColor = Color.Transparent,
        headlineColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
        supportingColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
        leadingIconColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
        trailingIconColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
    )
    val disabledReasonList = ListItemDefaults.colors(
        containerColor = Color.Transparent,
        headlineColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
        supportingColor = MaterialTheme.pixel.warning,
        leadingIconColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
        trailingIconColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
    )
    val enabledList = transparentListColors
    // A card the agent can't run keeps the yellow reason text; one greyed only
    // because a launch is already in flight is plainly inert.
    val listColorsFor: (Boolean) -> ListItemColors = { available ->
        when {
            !available -> disabledReasonList
            submitting -> disabledList
            else -> enabledList
        }
    }

    val listState = rememberLazyListState()
    val haptic = LocalHapticFeedback.current
    val modes = state.orderedModes
    // Moved in the drag's own frame; the saved order replaces it on change,
    // but not during a drag.
    var display by remember { mutableStateOf(modes) }
    val reorderState = rememberReorderableLazyListState(listState) { from, to ->
        display = display.movedByKey(from.key, to.key) { MODE_KEY + it.orderId }
        haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove)
    }
    LaunchedEffect(modes, reorderState.isAnyItemDragging) {
        if (!reorderState.isAnyItemDragging) display = modes
    }

    LazyColumn(
        state = listState,
        modifier = modifier,
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item(key = "mode_header") {
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(text = header.title, style = MaterialTheme.typography.titleMedium)
                header.subtitle?.let {
                    Text(
                        text = it,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Text(
                    text = REORDER_HINT,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        }
        itemsIndexed(display, key = { _, mode -> MODE_KEY + mode.orderId }) { index, mode ->
            ReorderableItem(reorderState, key = MODE_KEY + mode.orderId) { isDragging ->
                val handleModifier = Modifier.draggableHandle(
                    onDragStarted = { haptic.performHapticFeedback(HapticFeedbackType.LongPress) },
                    onDragStopped = { onReorder(display.map { it.orderId }) },
                )
                val customType = (mode as? CreateSessionMode.Custom)?.let { custom ->
                    state.customTypes.firstOrNull { it.id == custom.typeId }
                }
                val title = customType?.name ?: builtInModeTitle(mode)
                HandleRow(
                    handle = { WizardDragHandle(title, isDragging, handleModifier) },
                    reserveHandle = true,
                ) {
                    val cardModifier = Modifier.moveActions(index, display.size, onMove)
                    if (customType != null) {
                        CustomAgentTypeCard(
                            type = customType,
                            selected = state.selectedMode == mode || isDragging,
                            selectedColors = selectedCardColors,
                            listColors = listColorsFor(true),
                            onPick = { onChooseMode(mode) },
                            onEdit = { onEditCustomType(customType) },
                            onDelete = { onDeleteCustomType(customType) },
                            modifier = cardModifier,
                        )
                    } else {
                        BuiltInModeCard(
                            mode = mode,
                            state = state,
                            highlighted = state.selectedMode == mode || isDragging,
                            listColorsFor = listColorsFor,
                            onChoose = { onChooseMode(mode) },
                            modifier = cardModifier,
                        )
                    }
                }
            }
        }
        // Always enabled — the 20-type cap is not gated client-side (the cached
        // list can be stale); the editor surfaces the server's 409 inline instead.
        item(key = "add_custom_type") {
            HandleRow(handle = null, reserveHandle = true) {
                Card(
                    onClick = onAddCustomType,
                    enabled = !submitting,
                    border = BorderStroke(2.dp, MaterialTheme.pixel.surface2),
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag("add_custom_type_card"),
                ) {
                    ListItem(
                        headlineContent = { Text("Add custom agent…") },
                        supportingContent = { Text("Name + launch command, synced to your account") },
                        leadingContent = {
                            Icon(
                                imageVector = Icons.Filled.Add,
                                contentDescription = null,
                            )
                        },
                        colors = listColorsFor(true),
                    )
                }
            }
        }
        // Terminal and custom agent types on a non-git target launch straight from
        // this step, so it needs its own progress affordance — the config steps'
        // "Creating…" button never renders on that path.
        if (submitting) {
            item(key = "mode_pick_submitting") {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag("mode_pick_submitting"),
                    horizontalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterHorizontally),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    PixelBlinkCursor(
                        height = 20.dp,
                    )
                    Text(
                        text = "Creating session…",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

private fun builtInModeTitle(mode: CreateSessionMode): String = when (mode) {
    CreateSessionMode.Standard -> "Claude code session"
    CreateSessionMode.Codex -> "Codex session"
    CreateSessionMode.Antigravity -> "Antigravity session"
    CreateSessionMode.OpenCode -> "OpenCode session"
    CreateSessionMode.Copilot -> "GitHub Copilot session"
    CreateSessionMode.Cursor -> "Cursor session"
    CreateSessionMode.Junie -> "Junie session"
    CreateSessionMode.Pi -> "Pi session"
    CreateSessionMode.AutoScan -> "Auto Scan mode"
    CreateSessionMode.CustomShell -> "Terminal"
    is CreateSessionMode.Custom -> "Custom agent"
}

/** One built-in mode card: its copy, its availability, and its test tag. */
@Composable
private fun BuiltInModeCard(
    mode: CreateSessionMode,
    state: CreateSessionUiState,
    highlighted: Boolean,
    listColorsFor: (Boolean) -> ListItemColors,
    onChoose: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val available = state.isModeEnabled(mode)
    val (subtitle, tag) = when (mode) {
        CreateSessionMode.Standard -> Pair(
            if (state.claudeEnabled) "Configure and launch a Claude session."
            else "Claude CLI is not installed on the agent.",
            "mode_card_standard",
        )
        CreateSessionMode.Codex -> Pair(
            if (state.codexEnabled) "Configure and launch an OpenAI Codex session."
            else "Codex CLI is not installed on the agent.",
            "mode_card_codex",
        )
        CreateSessionMode.Antigravity -> Pair(
            if (state.antigravityEnabled) "Configure and launch a Google Antigravity session."
            else "Antigravity CLI (agy) is not installed on the agent.",
            "mode_card_antigravity",
        )
        CreateSessionMode.OpenCode -> Pair(
            when {
                !state.opencodeKnown -> "Update Mobaide on your PC to use OpenCode."
                !state.opencodeEnabled -> "OpenCode CLI is not installed on the agent."
                else -> "Configure and launch an OpenCode session."
            },
            "mode_card_opencode",
        )
        CreateSessionMode.Copilot -> Pair(
            when {
                !state.copilotKnown -> "Update Mobaide on your PC to use GitHub Copilot."
                !state.copilotEnabled -> "GitHub Copilot CLI is not installed on the agent."
                else -> "Configure and launch a GitHub Copilot session."
            },
            "mode_card_copilot",
        )
        CreateSessionMode.Cursor -> Pair(
            when {
                !state.cursorKnown -> "Update Mobaide on your PC to use Cursor."
                !state.cursorEnabled -> "Cursor CLI is not installed on the agent."
                else -> "Configure and launch a Cursor session."
            },
            "mode_card_cursor",
        )
        CreateSessionMode.Junie -> Pair(
            when {
                !state.junieKnown -> "Update Mobaide on your PC to use Junie."
                !state.junieEnabled -> "Junie CLI is not installed on the agent."
                else -> "Configure and launch a Junie session."
            },
            "mode_card_junie",
        )
        CreateSessionMode.Pi -> Pair(
            when {
                !state.piKnown -> "Update Mobaide on your PC to use Pi."
                !state.piEnabled -> "Pi is not installed on the agent."
                else -> "Configure and launch a Pi session."
            },
            "mode_card_pi",
        )
        CreateSessionMode.AutoScan -> Pair(
            when {
                state.autoScanCapability != null ->
                    if (state.autoScanEnabled) "Scan GitHub or Linear issues and launch agent sessions."
                    else "Auto Scan needs a saved workspace."
                !state.claudeEnabled -> "Auto Scan needs the Claude CLI, which isn't installed on the agent."
                !state.autoScanEnabled -> "Auto Scan needs a Linear API key for this workspace."
                else -> "Scan Linear for matching issues and launch sessions."
            },
            "mode_card_autoscan",
        )
        CreateSessionMode.CustomShell -> Pair("Bash / cmd in the chosen directory", "mode_card_terminal")
        is CreateSessionMode.Custom -> Pair("", "mode_card_custom")
    }
    Card(
        onClick = onChoose,
        enabled = available && !state.submitting,
        colors = if (highlighted) selectedCardColors else CardDefaults.cardColors(),
        border = BorderStroke(
            2.dp,
            if (highlighted) MaterialTheme.pixel.brand2 else MaterialTheme.pixel.surface2,
        ),
        modifier = modifier
            .fillMaxWidth()
            .testTag(tag),
    ) {
        ListItem(
            headlineContent = { Text(builtInModeTitle(mode)) },
            supportingContent = { Text(subtitle) },
            leadingContent = when (mode) {
                CreateSessionMode.CustomShell -> {
                    { Icon(imageVector = Icons.Filled.Terminal, contentDescription = null) }
                }
                CreateSessionMode.AutoScan -> {
                    { Icon(imageVector = Icons.Filled.Radar, contentDescription = null) }
                }
                else -> null
            },
            trailingContent = { ModeChevron() },
            colors = listColorsFor(available),
        )
    }
}

private const val CODEX_EFFORT_AGENT_DEFAULT = "Agent default"

/** Trailing disclosure chevron: tapping a mode card commits and moves on. */
@Composable
private fun ModeChevron() {
    Icon(
        imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
        contentDescription = null,
    )
}

/**
 * Mode-picker card for one account-scoped custom agent type. Tap commits the
 * mode; long-press opens the Edit/Delete context menu (`ElevatedCard`'s
 * clickable overload has no long-press support, hence `combinedClickable`).
 *
 * The card is never disabled — that would swallow the long-press menu too. A
 * launch already in flight is conveyed by [listColors] and enforced by
 * `CreateSessionViewModel.chooseMode`.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun CustomAgentTypeCard(
    type: CustomAgentTypeDto,
    selected: Boolean,
    selectedColors: CardColors,
    listColors: ListItemColors,
    onPick: () -> Unit,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var menuExpanded by remember { mutableStateOf(false) }
    Box(modifier = Modifier.fillMaxWidth()) {
        Card(
            colors = if (selected) selectedColors else CardDefaults.cardColors(),
            border = BorderStroke(
                2.dp,
                if (selected) MaterialTheme.pixel.brand2 else MaterialTheme.pixel.surface2,
            ),
            modifier = modifier
                .fillMaxWidth()
                .testTag("custom_type_card_${type.id}")
                .combinedClickable(
                    onClick = onPick,
                    onLongClick = { menuExpanded = true },
                    onLongClickLabel = "Edit or delete",
                ),
        ) {
            ListItem(
                headlineContent = { Text(type.name) },
                supportingContent = {
                    Text(
                        text = type.command,
                        fontFamily = ChromeFontFamily,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                leadingContent = {
                    Icon(
                        imageVector = Icons.Filled.SmartToy,
                        contentDescription = null,
                    )
                },
                trailingContent = { ModeChevron() },
                colors = listColors,
            )
        }
        DropdownMenu(
            expanded = menuExpanded,
            onDismissRequest = { menuExpanded = false },
        ) {
            DropdownMenuItem(
                text = { Text("Edit") },
                leadingIcon = { Icon(Icons.Filled.Edit, contentDescription = null) },
                onClick = {
                    menuExpanded = false
                    onEdit()
                },
            )
            DropdownMenuItem(
                text = { Text("Delete", color = MaterialTheme.colorScheme.error) },
                leadingIcon = {
                    Icon(
                        Icons.Filled.Delete,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.error,
                    )
                },
                onClick = {
                    menuExpanded = false
                    onDelete()
                },
            )
        }
    }
}

@Composable
private fun StandardConfigSection(
    state: CreateSessionUiState,
    showWorktreeToggle: Boolean,
    viewModel: CreateSessionViewModel,
    onContinueSession: () -> Unit,
) {
    AgentSessionViewField(
        terminalUi = state.claudeTerminalUi,
        lockedHint = state.claudeChatLockedHint,
        onTerminalUiChange = viewModel::toggleTerminalUi,
    )
    if (state.claudeResumeAvailable) {
        ContinueSessionCard(
            subtitle = "Open a Claude Code session started on your PC, for example in a terminal.",
            onClick = onContinueSession,
        )
    }
    ClaudeOptionControls(
        model = state.claudeModel,
        effort = state.claudeEffort,
        fastMode = state.claudeFastMode,
        onModelSelect = viewModel::setClaudeModel,
        onEffortSelect = viewModel::setClaudeEffort,
    )
    if (!state.claudeUsesChat) FocusViewToggle(state.focusMode, viewModel::toggleFocusMode)
    ToggleRow("Skip permissions", state.skipPermissions, viewModel::toggleSkipPermissions)

    if (showWorktreeToggle) {
        ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

        if (state.worktree) WorktreeBranchFields(state, viewModel)
    }

    ClaudeAdvancedOptions(
        noMemory = state.noMemory,
        fastMode = state.claudeFastMode,
        ultracode = state.claudeUltracode,
        debug = state.claudeDebug,
        appendSystemPrompt = state.claudeAppendSystemPrompt,
        onNoMemoryToggle = viewModel::toggleNoMemory,
        onFastModeToggle = viewModel::toggleClaudeFastMode,
        onUltracodeToggle = viewModel::toggleClaudeUltracode,
        onDebugToggle = viewModel::toggleClaudeDebug,
        onAppendSystemPromptChange = viewModel::setClaudeAppendSystemPrompt,
        showDebug = !state.claudeUsesChat,
        leadingContent = {
            CodeModeToggle(
                checked = state.claudeCodeMode,
                hint = CLAUDE_CODE_MODE_HINT,
                onChange = viewModel::toggleClaudeCodeMode,
            )
        },
    )

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submit,
        enabled = state.selection !is WorkspaceSelection.None && !state.submitting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
    }
}

/** "Continue a session" for the agents whose sessions open in the chat view. */
@Composable
private fun ContinueSessionCard(subtitle: String, onClick: () -> Unit) {
    WizardActionCard(
        title = "Continue a session",
        subtitle = subtitle,
        icon = Icons.Outlined.History,
        selected = false,
        onClick = onClick,
        contentDescription = "Continue a session",
        testTag = "continue_chat_session",
    )
}

/** Starts OpenCode's own provider sign-in in a terminal session. */
@Composable
private fun ConnectProviderCard(enabled: Boolean, onClick: () -> Unit) {
    WizardActionCard(
        title = "Connect a provider",
        subtitle = "Sign in to a model provider, for example Anthropic, OpenAI or Google. " +
            "OpenCode’s sign-in opens in a terminal and stays on your PC.",
        icon = Icons.AutoMirrored.Outlined.Login,
        selected = false,
        onClick = onClick,
        contentDescription = "Connect a provider",
        testTag = "opencode_connect_provider",
        enabled = enabled,
    )
}

/** An agent's collapsed-by-default "Advanced" options. */
@Composable
private fun AdvancedSection(content: @Composable () -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    PixelTextButton(
        onClick = { expanded = !expanded },
        modifier = Modifier
            .fillMaxWidth()
            .semantics { stateDescription = if (expanded) "Expanded" else "Collapsed" },
    ) {
        Icon(
            imageVector = if (expanded) Icons.Filled.KeyboardArrowUp else Icons.Filled.KeyboardArrowDown,
            contentDescription = null,
        )
        Text("Advanced")
    }
    if (expanded) content()
}

internal const val CLAUDE_CODE_MODE_HINT =
    "Experimental. Claude writes JavaScript that makes several tool calls in one step. " +
        "Sessions open in the terminal. Claude Code asks you to confirm until you accept; " +
        "turn Code Mode off here to stop the question."
internal const val OPENCODE_CODE_MODE_HINT =
    "OpenCode 2 calls MCP tools from JavaScript, several in one step. " +
        "Turn it off to have the model call each tool directly."
internal const val PI_CODE_MODE_HINT =
    "Adds Pi's codemode tool: the model writes JavaScript that calls several tools in one step."

/** "Code Mode": the agent writes JavaScript that makes several tool calls in one step. */
@Composable
internal fun CodeModeToggle(checked: Boolean, hint: String, onChange: (Boolean) -> Unit) {
    ToggleRow("Code Mode", checked, onChange)
    Text(
        text = hint,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** "Open sessions in" for an agent session: Chat is the same choice as Terminal UI off. */
@Composable
private fun AgentSessionViewField(terminalUi: Boolean, lockedHint: String?, onTerminalUiChange: (Boolean) -> Unit) {
    SessionViewField(
        chatSelected = !terminalUi && lockedHint == null,
        chatEnabled = lockedHint == null,
        onSelect = { chat -> onTerminalUiChange(!chat) },
        hint = lockedHint,
    )
}

/**
 * "Open sessions in": Chat or Terminal, shared by the agent steps and Auto
 * Scan. A disabled Chat shows Terminal selected.
 */
@Composable
internal fun SessionViewField(
    chatSelected: Boolean,
    chatEnabled: Boolean,
    onSelect: (chat: Boolean) -> Unit,
    hint: String? = null,
    hintModifier: Modifier = Modifier,
    testTag: String = "session_view",
) {
    Text("Open sessions in", style = MaterialTheme.typography.labelLarge)
    SingleChoiceSegmentedButtonRow(
        modifier = Modifier
            .fillMaxWidth()
            .testTag(testTag),
    ) {
        listOf(true to "Chat", false to "Terminal").forEach { (chat, label) ->
            SegmentedButton(
                selected = chatSelected == chat,
                onClick = { onSelect(chat) },
                enabled = !chat || chatEnabled,
                shape = RectangleShape,
                colors = pixelSegmentedButtonColors(),
                icon = {},
                modifier = Modifier.testTag(if (chat) "${testTag}_chat" else "${testTag}_terminal"),
            ) { Text(label) }
        }
    }
    hint?.let {
        Text(
            text = it,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = hintModifier,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CodexConfigSection(
    state: CreateSessionUiState,
    showWorktreeToggle: Boolean,
    viewModel: CreateSessionViewModel,
    onContinueSession: () -> Unit,
) {
    val preset = state.codexPreset

    AgentSessionViewField(
        terminalUi = state.codexTerminalUi,
        lockedHint = state.codexChatUnavailableHint,
        onTerminalUiChange = viewModel::toggleCodexTerminalUi,
    )
    if (state.codexResumeAvailable) {
        ContinueSessionCard(
            subtitle = "Open a Codex session started on your PC, for example in a terminal.",
            onClick = onContinueSession,
        )
    }

    CodexOptionControls(
        preset = preset,
        models = state.codexModels?.takeIf { it.isNotEmpty() } ?: CreateSessionViewModel.CODEX_MODELS,
        model = state.codexModel,
        effort = state.codexReasoningEffort,
        efforts = state.codexEffortsFor(state.codexModel),
        sandbox = state.codexSandbox,
        approval = state.codexApproval,
        fullAuto = state.codexFullAuto,
        onPreset = viewModel::selectCodexPreset,
        onModel = viewModel::setCodexModel,
        onEffort = viewModel::setCodexReasoningEffort,
        onSandbox = viewModel::setCodexSandbox,
        onApproval = viewModel::setCodexApproval,
    )

    if (showWorktreeToggle) {
        ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

        if (state.worktree) WorktreeBranchFields(state, viewModel)
    }

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitCodex,
        enabled = state.selection !is WorkspaceSelection.None && !state.submitting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
    }
}

/**
 * Codex's launch controls — autonomy presets, model, reasoning effort and the
 * Advanced sandbox/approval — shared by the Codex step and Auto Scan, which
 * keep their own values.
 */
@Composable
internal fun CodexOptionControls(
    preset: CodexAutonomyPreset?,
    models: List<String>,
    model: String?,
    effort: String?,
    efforts: List<String>,
    sandbox: String,
    approval: String,
    fullAuto: Boolean,
    onPreset: (CodexAutonomyPreset) -> Unit,
    onModel: (String?) -> Unit,
    onEffort: (String) -> Unit,
    onSandbox: (String) -> Unit,
    onApproval: (String) -> Unit,
    autonomyHint: String? = null,
    // Offers "Agent default" (a null effort: Codex uses its own setting).
    onAgentDefaultEffort: (() -> Unit)? = null,
) {
    Text("Autonomy", style = MaterialTheme.typography.labelLarge)
    val presets = listOf(
        CodexAutonomyPreset.ReadOnly to "Read-only",
        CodexAutonomyPreset.Auto to "Auto",
        CodexAutonomyPreset.FullAccess to "Full-access",
    )
    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
        presets.forEach { (value, label) ->
            SegmentedButton(
                selected = preset == value,
                onClick = { onPreset(value) },
                shape = RectangleShape,
                colors = pixelSegmentedButtonColors(),
                icon = {},
            ) { Text(label) }
        }
    }
    Text(
        text = when (preset) {
            CodexAutonomyPreset.ReadOnly -> "Reads the workspace; asks before any change."
            CodexAutonomyPreset.Auto -> "Edits within the workspace; asks before risky actions."
            CodexAutonomyPreset.FullAccess -> "No sandbox or approval prompts (--yolo)."
            null -> "Custom sandbox/approval set in Advanced."
        },
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    autonomyHint?.let { AskHint(it) }

    CliModelDropdown(
        models = models,
        selected = model,
        defaultLabel = "Default",
        onSelect = onModel,
    )

    // A dropdown, not the old 4-button segmented row: the agent-advertised
    // per-model sets run to 6 levels (low…ultra), which segments would truncate
    // on phone width.
    CodexEffortDropdown(
        selected = effort,
        advertised = efforts,
        onSelect = onEffort,
        onAgentDefault = onAgentDefaultEffort,
    )

    AdvancedSection {
        Text("Sandbox (-s)", style = MaterialTheme.typography.labelLarge)
        val sandboxes = listOf(
            "read-only" to "Read-only",
            "workspace-write" to "Workspace",
            "danger-full-access" to "Full",
        )
        SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
            sandboxes.forEach { (value, label) ->
                SegmentedButton(
                    selected = sandbox == value,
                    onClick = { onSandbox(value) },
                    enabled = !fullAuto,
                    shape = RectangleShape,
                    colors = pixelSegmentedButtonColors(),
                    icon = {},
                ) { Text(label) }
            }
        }
        Text("Approval (-a)", style = MaterialTheme.typography.labelLarge)
        val approvals = listOf(
            "on-request" to "On-request",
            "never" to "Never",
        )
        SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
            approvals.forEach { (value, label) ->
                SegmentedButton(
                    selected = approval == value,
                    onClick = { onApproval(value) },
                    enabled = !fullAuto,
                    shape = RectangleShape,
                    colors = pixelSegmentedButtonColors(),
                    icon = {},
                ) { Text(label) }
            }
        }
        if (fullAuto) {
            Text(
                text = "Sandbox and approval are ignored in Full-access (--yolo).",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/**
 * Model dropdown shared by the Codex and Antigravity config sections: a list of
 * quick-pick model names plus a leading null item ([defaultLabel]) that clears
 * the selection to the CLI's own default. Both sections pass a dynamic list
 * (falling back to a hardcoded constant when the agent doesn't enumerate).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CliModelDropdown(
    models: List<String>,
    selected: String?,
    defaultLabel: String,
    onSelect: (String?) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = !expanded },
    ) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = selected ?: defaultLabel,
            onValueChange = {},
            readOnly = true,
            label = { Text("Model") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            DropdownMenuItem(
                text = { Text(defaultLabel) },
                onClick = {
                    onSelect(null)
                    expanded = false
                },
            )
            models.forEach { model ->
                DropdownMenuItem(
                    text = { Text(model) },
                    onClick = {
                        onSelect(model)
                        expanded = false
                    },
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AntigravityConfigSection(
    state: CreateSessionUiState,
    showWorktreeToggle: Boolean,
    viewModel: CreateSessionViewModel,
    onContinueSession: () -> Unit,
) {
    AgentSessionViewField(
        terminalUi = state.antigravityTerminalUi,
        lockedHint = state.antigravityChatUnavailableHint,
        onTerminalUiChange = viewModel::toggleAntigravityTerminalUi,
    )
    if (state.antigravityResumeAvailable) {
        ContinueSessionCard(
            subtitle = "Open an Antigravity conversation started on your PC, for example with agy in a terminal.",
            onClick = onContinueSession,
        )
    }

    AntigravityOptionControls(
        models = state.antigravityModels?.takeIf { it.isNotEmpty() } ?: CreateSessionViewModel.ANTIGRAVITY_MODELS,
        model = state.antigravityModel,
        skipPermissions = state.antigravitySkipPermissions,
        sandbox = state.antigravitySandbox,
        addDirsDraft = state.antigravityAddDirsDraft,
        addDirsError = antigravityAddDirsError(state.antigravityAddDirsDraft),
        onModel = viewModel::setAntigravityModel,
        onSkipPermissions = viewModel::setAntigravitySkipPermissions,
        onSandbox = viewModel::setAntigravitySandbox,
        onAddDirsChange = viewModel::setAntigravityAddDirsDraft,
    )

    if (showWorktreeToggle) {
        ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

        if (state.worktree) WorktreeBranchFields(state, viewModel)
    }

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitAntigravity,
        enabled = state.selection !is WorkspaceSelection.None && !state.submitting &&
            antigravityAddDirsError(state.antigravityAddDirsDraft) == null,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
    }
}

/**
 * Antigravity's launch controls, shared by the Antigravity step and Auto Scan.
 * Skip permissions and Sandbox exclude each other; the setters enforce it.
 */
@Composable
internal fun AntigravityOptionControls(
    models: List<String>,
    model: String?,
    skipPermissions: Boolean,
    sandbox: Boolean,
    addDirsDraft: String,
    addDirsError: String?,
    onModel: (String?) -> Unit,
    onSkipPermissions: (Boolean) -> Unit,
    onSandbox: (Boolean) -> Unit,
    onAddDirsChange: (String) -> Unit,
    skipPermissionsHint: String? = null,
) {
    CliModelDropdown(
        models = models,
        selected = model,
        defaultLabel = "Auto",
        onSelect = onModel,
    )

    Text("Permissions", style = MaterialTheme.typography.labelLarge)
    ToggleRow(
        label = "Skip permissions (dangerous)",
        checked = skipPermissions,
        onChange = onSkipPermissions,
        enabled = !sandbox,
    )
    skipPermissionsHint?.let { AskHint(it) }
    ToggleRow(
        label = "Sandbox",
        checked = sandbox,
        onChange = onSandbox,
        enabled = !skipPermissions,
    )
    Text(
        text = when {
            skipPermissions ->
                "Auto-approves every tool permission request (--dangerously-skip-permissions)."
            sandbox ->
                "Runs with terminal restrictions enabled (--sandbox)."
            else ->
                "Interactive approvals (agy default). Skip permissions and Sandbox " +
                    "are mutually exclusive — together they would allow a sandbox escape."
        },
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )

    OutlinedTextField(
        colors = PixelTextFieldDefaults.colors(),
        value = addDirsDraft,
        onValueChange = onAddDirsChange,
        label = { Text("Additional directories (one per line, optional)") },
        isError = addDirsError != null,
        supportingText = { Text(addDirsError ?: "Each is added to the workspace.") },
        keyboardOptions = KeyboardOptions(
            capitalization = KeyboardCapitalization.None,
            autoCorrectEnabled = false,
        ),
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
private fun OpenCodeConfigSection(
    state: CreateSessionUiState,
    showWorktreeToggle: Boolean,
    viewModel: CreateSessionViewModel,
    onContinueSession: () -> Unit,
) {
    AgentSessionViewField(
        terminalUi = state.opencodeTerminalUi,
        lockedHint = state.opencodeChatUnavailableHint,
        onTerminalUiChange = viewModel::toggleOpenCodeTerminalUi,
    )
    if (state.opencodeResumeAvailable) {
        ContinueSessionCard(
            subtitle = "Open an OpenCode session started on your PC, for example in a terminal.",
            onClick = onContinueSession,
        )
    }
    ConnectProviderCard(
        enabled = !state.submitting,
        onClick = viewModel::connectOpenCodeProvider,
    )

    OpenCodePermissionControls(state.opencodeAutoApprove, viewModel::setOpenCodeAutoApprove)
    Text("Tools", style = MaterialTheme.typography.labelLarge)
    CodeModeToggle(
        checked = state.opencodeCodeMode,
        hint = OPENCODE_CODE_MODE_HINT,
        onChange = viewModel::setOpenCodeCodeMode,
    )

    if (showWorktreeToggle) {
        ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

        if (state.worktree) WorktreeBranchFields(state, viewModel)
    }

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitOpenCode,
        enabled = state.selection !is WorkspaceSelection.None && !state.submitting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
    }
}

@Composable
private fun CopilotConfigSection(
    state: CreateSessionUiState,
    showWorktreeToggle: Boolean,
    viewModel: CreateSessionViewModel,
    onContinueSession: () -> Unit,
) {
    AgentSessionViewField(
        terminalUi = state.copilotTerminalUi,
        lockedHint = state.copilotChatUnavailableHint,
        onTerminalUiChange = viewModel::toggleCopilotTerminalUi,
    )
    if (state.copilotResumeAvailable) {
        ContinueSessionCard(
            subtitle = "Open a GitHub Copilot session started on your PC, for example in a terminal.",
            onClick = onContinueSession,
        )
    }

    CopilotPermissionControls(state.copilotAllowAll, viewModel::setCopilotAllowAll)

    if (showWorktreeToggle) {
        ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

        if (state.worktree) WorktreeBranchFields(state, viewModel)
    }

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitCopilot,
        enabled = state.selection !is WorkspaceSelection.None && !state.submitting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
    }
}

@Composable
private fun CursorConfigSection(
    state: CreateSessionUiState,
    showWorktreeToggle: Boolean,
    viewModel: CreateSessionViewModel,
    onContinueSession: () -> Unit,
) {
    AgentSessionViewField(
        terminalUi = state.cursorTerminalUi,
        lockedHint = state.cursorChatUnavailableHint,
        onTerminalUiChange = viewModel::toggleCursorTerminalUi,
    )
    if (state.cursorResumeAvailable) {
        ContinueSessionCard(
            subtitle = "Open a Cursor chat session stored on your PC. Sessions from Cursor’s terminal UI " +
                "are not listed.",
            onClick = onContinueSession,
        )
    }

    CursorPermissionControls(state.cursorForce, viewModel::setCursorForce)

    if (showWorktreeToggle) {
        ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

        if (state.worktree) WorktreeBranchFields(state, viewModel)
    }

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitCursor,
        enabled = state.selection !is WorkspaceSelection.None && !state.submitting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
    }
}

@Composable
private fun JunieConfigSection(
    state: CreateSessionUiState,
    showWorktreeToggle: Boolean,
    viewModel: CreateSessionViewModel,
    onContinueSession: () -> Unit,
) {
    AgentSessionViewField(
        terminalUi = state.junieTerminalUi,
        lockedHint = state.junieChatUnavailableHint,
        onTerminalUiChange = viewModel::toggleJunieTerminalUi,
    )
    if (state.junieResumeAvailable) {
        ContinueSessionCard(
            subtitle = "Open a Junie session stored on your PC, also one you started in Junie’s terminal UI.",
            onClick = onContinueSession,
        )
    }

    JunieOptionControls(
        planMode = state.juniePlanMode,
        brave = state.junieBrave,
        onPlanModeChange = viewModel::setJuniePlanMode,
        onBraveChange = viewModel::setJunieBrave,
    )

    if (showWorktreeToggle) {
        ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

        if (state.worktree) WorktreeBranchFields(state, viewModel)
    }

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitJunie,
        enabled = state.selection !is WorkspaceSelection.None && !state.submitting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
    }
}

/** OpenCode's `--auto` switch, shared by the OpenCode step and Auto Scan. */
@Composable
internal fun OpenCodePermissionControls(autoApprove: Boolean, onChange: (Boolean) -> Unit) {
    Text("Permissions", style = MaterialTheme.typography.labelLarge)
    ToggleRow(
        label = "Auto-approve permissions (dangerous)",
        checked = autoApprove,
        onChange = onChange,
    )
    Text(
        text = "OpenCode accepts every request that your rules do not deny.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** Copilot's `--allow-all` switch, shared by the Copilot step and Auto Scan. */
@Composable
internal fun CopilotPermissionControls(allowAll: Boolean, onChange: (Boolean) -> Unit) {
    Text("Permissions", style = MaterialTheme.typography.labelLarge)
    ToggleRow(
        label = "Allow all tools, paths and URLs (dangerous)",
        checked = allowAll,
        onChange = onChange,
    )
    Text(
        text = "When on, Copilot does not ask before it runs commands, edits files or opens URLs.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** Cursor's `--force` switch, shared by the Cursor step and Auto Scan. */
@Composable
internal fun CursorPermissionControls(force: Boolean, onChange: (Boolean) -> Unit) {
    Text("Permissions", style = MaterialTheme.typography.labelLarge)
    ToggleRow(
        label = "Run everything without asking (dangerous)",
        checked = force,
        onChange = onChange,
    )
    Text(
        text = "When on, Cursor runs commands without asking, except commands that its settings deny.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** Junie's Plan mode and Brave mode, shared by the Junie step and Auto Scan. */
@Composable
internal fun JunieOptionControls(
    planMode: Boolean,
    brave: Boolean,
    onPlanModeChange: (Boolean) -> Unit,
    onBraveChange: (Boolean) -> Unit,
) {
    Text("Mode", style = MaterialTheme.typography.labelLarge)
    ToggleRow(
        label = "Start in Plan mode",
        checked = planMode,
        onChange = onPlanModeChange,
    )
    Text(
        text = "Junie prepares a plan and asks you to approve it before it changes your files.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )

    Text("Permissions", style = MaterialTheme.typography.labelLarge)
    ToggleRow(
        label = "Brave mode (dangerous)",
        checked = brave,
        onChange = onBraveChange,
    )
    Text(
        text = if (brave) {
            "Junie runs all commands without asking, in this session only."
        } else {
            "Junie uses its own Brave setting. By default it asks before commands that are not safe."
        },
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun PiConfigSection(
    state: CreateSessionUiState,
    showWorktreeToggle: Boolean,
    viewModel: CreateSessionViewModel,
    onContinueSession: () -> Unit,
) {
    AgentSessionViewField(
        terminalUi = state.piTerminalUi,
        lockedHint = state.piChatUnavailableHint,
        onTerminalUiChange = viewModel::togglePiTerminalUi,
    )
    if (state.piResumeAvailable) {
        ContinueSessionCard(
            subtitle = "Open a Pi session started on your PC, for example in a terminal.",
            onClick = onContinueSession,
        )
    }

    // Pi has no approval mode: say what a user of the other agents would look
    // for here.
    Text("Permissions", style = MaterialTheme.typography.labelLarge)
    Text(
        text = "Pi runs commands and edits files without asking. " +
            "Pick the model and the thinking level inside the session.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Text("Tools", style = MaterialTheme.typography.labelLarge)
    CodeModeToggle(
        checked = state.piCodeMode,
        hint = PI_CODE_MODE_HINT,
        onChange = viewModel::setPiCodeMode,
    )

    if (showWorktreeToggle) {
        ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

        if (state.worktree) WorktreeBranchFields(state, viewModel)
    }

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitPi,
        enabled = state.selection !is WorkspaceSelection.None && !state.submitting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
    }
}

/**
 * Claude reasoning controls shared by the Standard and Auto Scan config steps:
 * a model dropdown and a model-conditional effort dropdown. The rest of the
 * Claude flags live in [ClaudeAdvancedOptions], placed after the main options.
 */
@Composable
private fun ClaudeOptionControls(
    model: String?,
    effort: String,
    fastMode: Boolean,
    onModelSelect: (String?) -> Unit,
    onEffortSelect: (String) -> Unit,
) {
    ClaudeModelDropdown(
        selected = model,
        enabled = !fastMode,
        onSelect = onModelSelect,
    )
    if (fastMode) {
        Text(
            text = "Fast mode runs on Opus.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }

    ClaudeEffortDropdown(
        selected = effort,
        model = model,
        onSelect = onEffortSelect,
    )
}

@Composable
private fun FocusViewToggle(checked: Boolean, onToggle: (Boolean) -> Unit) {
    ToggleRow("Focus view", checked, onToggle)
    Text(
        text = "Claude Code shows only your last prompt, a one-line summary of tool calls, and the final response.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** Collapsed-by-default expander for the less common Claude flags, shared by the Standard and Auto Scan steps. */
@Composable
private fun ClaudeAdvancedOptions(
    noMemory: Boolean,
    fastMode: Boolean,
    ultracode: Boolean,
    debug: Boolean,
    appendSystemPrompt: String,
    onNoMemoryToggle: (Boolean) -> Unit,
    onFastModeToggle: (Boolean) -> Unit,
    onUltracodeToggle: (Boolean) -> Unit,
    onDebugToggle: (Boolean) -> Unit,
    onAppendSystemPromptChange: (String) -> Unit,
    showDebug: Boolean = true,
    leadingContent: @Composable () -> Unit = {},
) {
    AdvancedSection {
        leadingContent()
        ToggleRow("No memory", noMemory, onNoMemoryToggle)
        ToggleRow("Fast mode (Opus only)", fastMode, onFastModeToggle)
        ToggleRow("Ultracode", ultracode, onUltracodeToggle)
        if (showDebug) {
            ToggleRow("Debug logging", debug, onDebugToggle)
            Text(
                text = "Claude Code prints verbose diagnostics into the terminal.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = appendSystemPrompt,
            onValueChange = onAppendSystemPromptChange,
            label = { Text("Append system prompt (optional)") },
            modifier = Modifier.fillMaxWidth(),
            minLines = 2,
            maxLines = 6,
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.None,
                autoCorrectEnabled = false,
            ),
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ClaudeModelDropdown(
    selected: String?,
    enabled: Boolean,
    onSelect: (String?) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = expanded && enabled,
        onExpandedChange = { if (enabled) expanded = !expanded },
    ) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = selected ?: "Default",
            onValueChange = {},
            readOnly = true,
            enabled = enabled,
            label = { Text("Model") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        DropdownMenu(
            expanded = expanded && enabled,
            onDismissRequest = { expanded = false },
        ) {
            DropdownMenuItem(
                text = { Text("Default") },
                onClick = {
                    onSelect(null)
                    expanded = false
                },
            )
            CreateSessionViewModel.CLAUDE_MODELS.forEach { model ->
                DropdownMenuItem(
                    text = { Text(model) },
                    onClick = {
                        onSelect(model)
                        expanded = false
                    },
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ClaudeEffortDropdown(
    selected: String,
    model: String?,
    onSelect: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val options = CreateSessionViewModel.claudeEffortsFor(model)
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = !expanded },
    ) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = selected.replaceFirstChar { it.uppercase() },
            onValueChange = {},
            readOnly = true,
            label = { Text("Effort") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            options.forEach { effort ->
                DropdownMenuItem(
                    text = { Text(effort.replaceFirstChar { it.uppercase() }) },
                    onClick = {
                        onSelect(effort)
                        expanded = false
                    },
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CodexEffortDropdown(
    selected: String?,
    advertised: List<String>,
    onSelect: (String) -> Unit,
    onAgentDefault: (() -> Unit)? = null,
) {
    var expanded by remember { mutableStateOf(false) }
    // Keep-selected repair: a restored-prefs or late-probe effort outside the
    // advertised set must stay in the menu, or the field would display a value
    // its own options cannot reproduce while the request still sends it.
    val options = if (selected == null || selected in advertised) advertised else advertised + selected
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = !expanded },
    ) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = selected?.replaceFirstChar { it.uppercase() } ?: CODEX_EFFORT_AGENT_DEFAULT,
            onValueChange = {},
            readOnly = true,
            label = { Text("Reasoning effort") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            if (onAgentDefault != null) {
                DropdownMenuItem(
                    text = { Text(CODEX_EFFORT_AGENT_DEFAULT) },
                    onClick = {
                        onAgentDefault()
                        expanded = false
                    },
                )
            }
            options.forEach { effort ->
                DropdownMenuItem(
                    text = { Text(effort.replaceFirstChar { it.uppercase() }) },
                    onClick = {
                        onSelect(effort)
                        expanded = false
                    },
                )
            }
        }
    }
}

/**
 * The worktree's new branch name and, behind `capabilities.git.worktree_base`,
 * the branch it starts from.
 */
@Composable
private fun WorktreeBranchFields(
    state: CreateSessionUiState,
    viewModel: CreateSessionViewModel,
) {
    var basePickerOpen by rememberSaveable { mutableStateOf(false) }
    OutlinedTextField(
        colors = PixelTextFieldDefaults.colors(),
        value = state.branchName,
        onValueChange = viewModel::setBranchName,
        label = { Text("Branch name (optional)") },
        singleLine = true,
        keyboardOptions = KeyboardOptions(
            capitalization = KeyboardCapitalization.None,
            autoCorrectEnabled = false,
        ),
        modifier = Modifier.fillMaxWidth(),
    )
    if (!state.showBaseBranchPicker) return
    BaseBranchField(
        load = state.baseBranches,
        selected = state.baseBranch,
        onOpen = { basePickerOpen = true },
        onRetry = viewModel::retryBaseBranches,
    )
    val loaded = state.baseBranches as? BaseBranchesLoad.Loaded
    if (basePickerOpen && loaded != null) {
        BaseBranchSheet(
            response = loaded.response,
            selected = state.baseBranch,
            onSelect = viewModel::selectBaseBranch,
            onDismiss = { basePickerOpen = false },
        )
    }
}

@Composable
private fun TerminalConfigSection(
    state: CreateSessionUiState,
    viewModel: CreateSessionViewModel,
) {
    ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

    if (state.worktree) WorktreeBranchFields(state, viewModel)

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitFromTerminalConfig,
        enabled = state.selection !is WorkspaceSelection.None && !state.submitting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
    }
}

@Composable
private fun AutoScanConfigSection(
    state: CreateSessionUiState,
    viewModel: CreateSessionViewModel,
) {
    val capability = state.autoScanCapability
    if (capability != null) {
        AutoScanWizardSection(state = state, capability = capability, viewModel = viewModel)
        return
    }
    if (state.autoScanProbed) {
        Text(
            text = "Update Mobaide on your PC to scan GitHub issues and choose the agent.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag("autoscan_update_agent_note"),
        )
    }
    AutoScanClaudeOptions(state = state, viewModel = viewModel)

    AutoScanInitialPromptField(state.initialPrompt, viewModel::setInitialPrompt)

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitAutoScan,
        enabled = state.selectedWorkspaceName != null && !state.autoScanStarting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.autoScanStarting) "Starting…" else "Start Auto Scan")
    }
}

/** Claude Code's Auto Scan options, from the Auto Scan launch-prefs bucket. */
@Composable
internal fun AutoScanClaudeOptions(
    state: CreateSessionUiState,
    viewModel: CreateSessionViewModel,
) {
    ClaudeOptionControls(
        model = state.autoScanClaudeModel,
        effort = state.autoScanClaudeEffort,
        fastMode = state.autoScanClaudeFastMode,
        onModelSelect = viewModel::setAutoScanClaudeModel,
        onEffortSelect = viewModel::setAutoScanClaudeEffort,
    )
    ToggleRow("Plan mode", state.autoScanPlanMode, viewModel::toggleAutoScanPlanMode)
    if (state.autoScanUi != AUTO_SCAN_UI_CHAT) FocusViewToggle(state.autoScanFocusMode, viewModel::toggleAutoScanFocusMode)
    ToggleRow(
        "Skip permissions",
        state.autoScanSkipPermissions,
        viewModel::toggleAutoScanSkipPermissions,
    )
    if (!state.autoScanSkipPermissions) AskHint(RUN_WITHOUT_ASKING_OFF_HINT)
    ClaudeAdvancedOptions(
        noMemory = state.autoScanNoMemory,
        fastMode = state.autoScanClaudeFastMode,
        ultracode = state.autoScanClaudeUltracode,
        debug = state.autoScanClaudeDebug,
        appendSystemPrompt = state.autoScanClaudeAppendSystemPrompt,
        onNoMemoryToggle = viewModel::toggleAutoScanNoMemory,
        onFastModeToggle = viewModel::toggleAutoScanClaudeFastMode,
        onUltracodeToggle = viewModel::toggleAutoScanClaudeUltracode,
        onDebugToggle = viewModel::toggleAutoScanClaudeDebug,
        onAppendSystemPromptChange = viewModel::setAutoScanClaudeAppendSystemPrompt,
        leadingContent = {
            CodeModeToggle(
                checked = state.autoScanClaudeCodeMode,
                hint = CLAUDE_CODE_MODE_HINT,
                onChange = viewModel::toggleAutoScanClaudeCodeMode,
            )
        },
    )
}

/** What a scan costs when its agent may ask before acting, in the attention colour. */
@Composable
internal fun AskHint(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.pixel.warning,
        modifier = modifier,
    )
}

@Composable
internal fun AutoScanInitialPromptField(
    value: String,
    onChange: (String) -> Unit,
    label: String = "Initial prompt (optional)",
) {
    OutlinedTextField(
        colors = PixelTextFieldDefaults.colors(),
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        modifier = Modifier
            .fillMaxWidth()
            .testTag("autoscan_initial_prompt"),
        minLines = 3,
        maxLines = 8,
        keyboardOptions = KeyboardOptions(
            capitalization = KeyboardCapitalization.None,
            autoCorrectEnabled = false,
        ),
    )
}

@Composable
internal fun ToggleRow(
    label: String,
    checked: Boolean,
    onChange: (Boolean) -> Unit,
    enabled: Boolean = true,
) {
    // One toggleable row: TalkBack reads the label with the switch, and the
    // label is a tap target too.
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .toggleable(
                value = checked,
                enabled = enabled,
                role = Role.Switch,
                onValueChange = onChange,
            )
            .heightIn(min = 48.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = label,
            color = if (enabled) Color.Unspecified
            else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
            modifier = Modifier.weight(1f),
        )
        Switch(
            checked = checked,
            onCheckedChange = null,
            enabled = enabled,
            colors = pixelSwitchColors(),
        )
    }
}
