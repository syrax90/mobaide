package com.mobaide.app.ui.sessions

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
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material.icons.filled.Terminal
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
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.HorizontalDivider
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
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import com.mobaide.app.ui.theme.ChromeFontFamily
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.ui.common.PATH_BROWSE_HINT
import com.mobaide.app.ui.common.PathSuggestionsUi
import com.mobaide.app.ui.common.PathTextField
import com.mobaide.app.ui.common.SecretTextField
import com.mobaide.app.ui.projects.ProjectFormSheet
import com.mobaide.app.ui.projects.ProjectsUiEvent
import com.mobaide.app.ui.projects.ProjectsViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CreateSessionScreen(
    viewModel: CreateSessionViewModel,
    projectsViewModel: ProjectsViewModel,
    onBack: () -> Unit,
    onSessionCreated: (String) -> Unit,
    onAutoScanStarted: (String) -> Unit,
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val projectsState by projectsViewModel.uiState.collectAsStateWithLifecycle()
    val customPathSuggestions by viewModel.customPathSuggestionState.collectAsStateWithLifecycle()
    val projectPathSuggestions by projectsViewModel.pathSuggestionState.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(viewModel) {
        viewModel.navigateToSession.collect { sessionId -> onSessionCreated(sessionId) }
    }
    LaunchedEffect(viewModel) {
        viewModel.navigateToAutoScan.collect { projectName -> onAutoScanStarted(projectName) }
    }
    LaunchedEffect(viewModel) {
        viewModel.transientMessages.collect { msg -> snackbar.showSnackbar(msg) }
    }
    LaunchedEffect(projectsViewModel) {
        projectsViewModel.projectCreated.collect { newProject ->
            viewModel.onProjectCreated(newProject)
        }
    }
    LaunchedEffect(projectsViewModel) {
        // Only Message events apply to the create-only flow on this screen.
        // DeletedWithUndo / LinearKeyReminder / CloseSheet belong to ProjectsScreen.
        projectsViewModel.events.collect { event ->
            if (event is ProjectsUiEvent.Message) snackbar.showSnackbar(event.text)
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
    BackHandler(enabled = state.step != CreateSessionStep.ProjectSelect) {
        viewModel.goBack()
    }

    val onTopBarBack: () -> Unit = {
        if (state.step == CreateSessionStep.ProjectSelect) onBack() else viewModel.goBack()
    }

    PixelScaffold(
        topBar = {
            PixelTopBar(
                title = {
                    Text(
                        when (state.step) {
                            CreateSessionStep.ProjectSelect -> "New Session"
                            CreateSessionStep.ModePick -> "Select mode"
                            CreateSessionStep.StandardConfig -> "Claude code session"
                            CreateSessionStep.CodexConfig -> "Codex session"
                            CreateSessionStep.AntigravityConfig -> "Antigravity session"
                            CreateSessionStep.AutoScanConfig -> "Auto Scan mode"
                            // TerminalConfig is shared between Terminal mode and
                            // custom agent types — title by the selected mode.
                            CreateSessionStep.TerminalConfig ->
                                (state.selectedMode as? CreateSessionMode.Custom)
                                    ?.let { mode ->
                                        state.customTypes
                                            .firstOrNull { it.id == mode.typeId }?.name
                                    }
                                    ?: "Terminal session"
                        }
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
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
                // The app is edge-to-edge, so adjustResize never shrinks the window: the
                // keyboard arrives only as an inset. Consume it above verticalScroll so the
                // viewport shrinks and the focused field scrolls clear of the keyboard.
                // Scaffold already inset this content by systemBars — add only the excess.
                .windowInsetsPadding(WindowInsets.ime.exclude(WindowInsets.systemBars))
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            val storedProject = (state.selection as? ProjectSelection.Stored)?.let { sel ->
                state.projects.firstOrNull { it.name == sel.name }
            }
            val customPathSelected = state.selection as? ProjectSelection.CustomPath
            val modeHeader = when (val sel = state.selection) {
                is ProjectSelection.Stored -> ModeHeader(
                    title = storedProject?.name ?: sel.name,
                    subtitle = storedProject?.path,
                )
                is ProjectSelection.CustomPath -> ModeHeader(
                    title = "Custom path",
                    subtitle = sel.path,
                )
                ProjectSelection.None -> ModeHeader(
                    title = "",
                    subtitle = null,
                )
            }

            when (state.step) {
                CreateSessionStep.ProjectSelect -> ProjectSelectSection(
                    state = state,
                    selectedProject = storedProject,
                    customPathSelected = customPathSelected,
                    onSelect = viewModel::selectProject,
                    onCustomPathSelected = viewModel::selectCustomPath,
                    onCustomPathChange = viewModel::setCustomPathDraft,
                    onPickRecentCustomPath = viewModel::pickRecentCustomPath,
                    onRemoveRecentCustomPath = viewModel::removeRecentCustomPath,
                    customPathSuggestions = customPathSuggestions,
                    onCustomPathFocusChange = viewModel::onCustomPathFocusChange,
                    onPickCustomPathSuggestion = viewModel::pickCustomPathSuggestion,
                    onCreateProjectClicked = projectsViewModel::openCreateSheet,
                    onContinue = viewModel::confirmProject,
                )
                CreateSessionStep.ModePick -> ModePickerSection(
                    header = modeHeader,
                    selectedMode = state.selectedMode,
                    claudeEnabled = state.claudeEnabled,
                    codexEnabled = state.codexEnabled,
                    antigravityEnabled = state.antigravityEnabled,
                    autoScanEnabled = state.autoScanEnabled,
                    submitting = state.submitting,
                    customTypes = state.customTypes,
                    onChooseMode = viewModel::chooseMode,
                    onAddCustomType = { viewModel.openTypeEditor(null) },
                    onEditCustomType = viewModel::openTypeEditor,
                    onDeleteCustomType = viewModel::requestDeleteType,
                )
                CreateSessionStep.StandardConfig -> StandardConfigSection(
                    state = state,
                    showWorktreeToggle = state.selection is ProjectSelection.Stored &&
                        storedProject?.is_git == true,
                    viewModel = viewModel,
                )
                CreateSessionStep.CodexConfig -> CodexConfigSection(
                    state = state,
                    showWorktreeToggle = state.selection is ProjectSelection.Stored &&
                        storedProject?.is_git == true,
                    viewModel = viewModel,
                )
                CreateSessionStep.AntigravityConfig -> AntigravityConfigSection(
                    state = state,
                    showWorktreeToggle = state.selection is ProjectSelection.Stored &&
                        storedProject?.is_git == true,
                    viewModel = viewModel,
                )
                CreateSessionStep.AutoScanConfig -> AutoScanConfigSection(
                    state = state,
                    viewModel = viewModel,
                )
                CreateSessionStep.TerminalConfig -> TerminalConfigSection(
                    state = state,
                    viewModel = viewModel,
                )
            }
        }
    }

    val form = projectsState.form
    if (form != null) {
        ProjectFormSheet(
            state = form,
            onDismiss = projectsViewModel::closeSheet,
            onNameChange = projectsViewModel::onNameChange,
            onPathChange = projectsViewModel::onPathChange,
            onCondaChange = projectsViewModel::onCondaChange,
            onLinearKeyChange = projectsViewModel::onLinearKeyChange,
            onToggleKeyVisible = projectsViewModel::toggleLinearKeyVisible,
            onRequestClearLinearKey = projectsViewModel::requestClearLinearKey,
            onTouch = projectsViewModel::touch,
            onAddFlag = projectsViewModel::addFlag,
            onUpdateFlag = projectsViewModel::updateFlag,
            onRemoveFlag = projectsViewModel::removeFlag,
            onToggleTriggerLabel = projectsViewModel::toggleTriggerLabel,
            onToggleTriggerState = projectsViewModel::toggleTriggerState,
            onActiveStateChange = projectsViewModel::onActiveStateChange,
            onDoneStateChange = projectsViewModel::onDoneStateChange,
            onRemovalLabelChange = projectsViewModel::onRemovalLabelChange,
            onRefreshFilterMetadata = projectsViewModel::refreshFilterMetadata,
            onSave = projectsViewModel::save,
            pathSuggestions = projectPathSuggestions,
            onPathFocusChange = projectsViewModel::onPathFocusChange,
            onPickPathSuggestion = projectsViewModel::pickPathSuggestion,
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

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun ProjectSelectSection(
    state: CreateSessionUiState,
    selectedProject: com.mobaide.app.data.network.dto.ProjectInfo?,
    customPathSelected: ProjectSelection.CustomPath?,
    onSelect: (String) -> Unit,
    onCustomPathSelected: () -> Unit,
    onCustomPathChange: (String) -> Unit,
    onPickRecentCustomPath: (String) -> Unit,
    onRemoveRecentCustomPath: (String) -> Unit,
    customPathSuggestions: PathSuggestionsUi,
    onCustomPathFocusChange: (Boolean) -> Unit,
    onPickCustomPathSuggestion: (String) -> Unit,
    onCreateProjectClicked: () -> Unit,
    onContinue: () -> Unit,
) {
    var dropdownExpanded by remember { mutableStateOf(false) }
    val displayValue = when (val sel = state.selection) {
        is ProjectSelection.Stored -> sel.name
        is ProjectSelection.CustomPath -> "Custom path"
        ProjectSelection.None ->
            if (state.isLoadingProjects) "Loading…" else "Select project"
    }
    ExposedDropdownMenuBox(
        expanded = dropdownExpanded,
        onExpandedChange = { dropdownExpanded = !dropdownExpanded },
    ) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = displayValue,
            onValueChange = {},
            readOnly = true,
            label = { Text("Project") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = dropdownExpanded) },
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        DropdownMenu(
            expanded = dropdownExpanded,
            onDismissRequest = { dropdownExpanded = false },
        ) {
            state.projects.forEach { project ->
                DropdownMenuItem(
                    text = { Text(project.name) },
                    onClick = {
                        onSelect(project.name)
                        dropdownExpanded = false
                    },
                )
            }
            if (state.projects.isNotEmpty()) {
                HorizontalDivider(thickness = 2.dp)
            }
            DropdownMenuItem(
                text = {
                    Column {
                        Text("Custom path…")
                        Text(
                            text = "Run once — no project is created",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                leadingIcon = {
                    Icon(Icons.Default.FolderOpen, contentDescription = null)
                },
                modifier = Modifier.semantics {
                    role = Role.Button
                    contentDescription = "Custom path"
                },
                onClick = {
                    dropdownExpanded = false
                    onCustomPathSelected()
                },
            )
            DropdownMenuItem(
                text = { Text("Create new project…") },
                leadingIcon = {
                    Icon(Icons.Default.Add, contentDescription = null)
                },
                modifier = Modifier.semantics {
                    role = Role.Button
                    contentDescription = "Create new project"
                },
                onClick = {
                    dropdownExpanded = false
                    onCreateProjectClicked()
                },
            )
        }
    }

    selectedProject?.let { project ->
        Text(
            text = project.path,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (project.has_linear_key) {
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
                Text(
                    text = "Linear key configured",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }

    if (customPathSelected != null) {
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

    val canContinue = when (val sel = state.selection) {
        is ProjectSelection.Stored -> true
        is ProjectSelection.CustomPath ->
            state.customPathDraft.isNotBlank() && state.customPathError == null
        ProjectSelection.None -> false
    }
    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = onContinue,
        enabled = canContinue,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text("Continue")
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
            Text(
                text = state.customPathError
                    ?: "e.g. /home/user/code or D:\\projects\\demo — $PATH_BROWSE_HINT",
            )
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
 *  [ProjectSelection]. */
private data class ModeHeader(
    val title: String,
    val subtitle: String?,
)

/**
 * Mode picker. There is no confirm button: a card tap commits the choice and
 * moves the wizard on (`onChooseMode`), so each card carries a trailing chevron
 * to read as navigation. The selected highlight survives for the return trip
 * via Back.
 */
@Composable
private fun ModePickerSection(
    header: ModeHeader,
    selectedMode: CreateSessionMode?,
    claudeEnabled: Boolean,
    codexEnabled: Boolean,
    antigravityEnabled: Boolean,
    autoScanEnabled: Boolean,
    submitting: Boolean,
    customTypes: List<CustomAgentTypeDto>,
    onChooseMode: (CreateSessionMode) -> Unit,
    onAddCustomType: () -> Unit,
    onEditCustomType: (CustomAgentTypeDto) -> Unit,
    onDeleteCustomType: (CustomAgentTypeDto) -> Unit,
) {
    Text(
        text = header.title,
        style = MaterialTheme.typography.titleMedium,
    )
    header.subtitle?.let {
        Text(
            text = it,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }

    val selectedColors = CardDefaults.cardColors(
        containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
    )
    // Transparent container so the ElevatedCard (incl. its dimmed disabled
    // colour) shows through; the disabled variant also fades the text to the
    // Material disabled-content alpha so a grayed-out card reads as inert.
    val enabledList = ListItemDefaults.colors(
        containerColor = Color.Transparent,
    )
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
    // A card the agent can't run keeps the yellow reason text; one greyed only
    // because a launch is already in flight is plainly inert.
    val listColorsFor: (Boolean) -> ListItemColors = { available ->
        when {
            !available -> disabledReasonList
            submitting -> disabledList
            else -> enabledList
        }
    }

    Card(
        onClick = { onChooseMode(CreateSessionMode.Standard) },
        enabled = claudeEnabled && !submitting,
        colors = if (selectedMode == CreateSessionMode.Standard) selectedColors else CardDefaults.cardColors(),
        border = BorderStroke(
            2.dp,
            if (selectedMode == CreateSessionMode.Standard) MaterialTheme.pixel.brand2
            else MaterialTheme.pixel.surface2,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .testTag("mode_card_standard"),
    ) {
        ListItem(
            headlineContent = { Text("Claude code session") },
            supportingContent = {
                Text(
                    if (claudeEnabled) "Configure and launch a Claude session."
                    else "Claude CLI is not installed on the agent.",
                )
            },
            trailingContent = { ModeChevron() },
            colors = listColorsFor(claudeEnabled),
        )
    }

    Card(
        onClick = { onChooseMode(CreateSessionMode.Codex) },
        enabled = codexEnabled && !submitting,
        colors = if (selectedMode == CreateSessionMode.Codex) selectedColors else CardDefaults.cardColors(),
        border = BorderStroke(
            2.dp,
            if (selectedMode == CreateSessionMode.Codex) MaterialTheme.pixel.brand2
            else MaterialTheme.pixel.surface2,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .testTag("mode_card_codex"),
    ) {
        ListItem(
            headlineContent = { Text("Codex session") },
            supportingContent = {
                Text(
                    if (codexEnabled) "Configure and launch an OpenAI Codex session."
                    else "Codex CLI is not installed on the agent.",
                )
            },
            trailingContent = { ModeChevron() },
            colors = listColorsFor(codexEnabled),
        )
    }

    Card(
        onClick = { onChooseMode(CreateSessionMode.Antigravity) },
        enabled = antigravityEnabled && !submitting,
        colors = if (selectedMode == CreateSessionMode.Antigravity) selectedColors else CardDefaults.cardColors(),
        border = BorderStroke(
            2.dp,
            if (selectedMode == CreateSessionMode.Antigravity) MaterialTheme.pixel.brand2
            else MaterialTheme.pixel.surface2,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .testTag("mode_card_antigravity"),
    ) {
        ListItem(
            headlineContent = { Text("Antigravity session") },
            supportingContent = {
                Text(
                    if (antigravityEnabled) "Configure and launch a Google Antigravity session."
                    else "Antigravity CLI (agy) is not installed on the agent.",
                )
            },
            trailingContent = { ModeChevron() },
            colors = listColorsFor(antigravityEnabled),
        )
    }

    Card(
        onClick = { onChooseMode(CreateSessionMode.AutoScan) },
        enabled = autoScanEnabled && !submitting,
        colors = if (selectedMode == CreateSessionMode.AutoScan) selectedColors else CardDefaults.cardColors(),
        border = BorderStroke(
            2.dp,
            if (selectedMode == CreateSessionMode.AutoScan) MaterialTheme.pixel.brand2
            else MaterialTheme.pixel.surface2,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .testTag("mode_card_autoscan"),
    ) {
        ListItem(
            headlineContent = { Text("Auto Scan mode") },
            supportingContent = {
                Text(
                    when {
                        !claudeEnabled -> "Auto Scan needs the Claude CLI, which isn't installed on the agent."
                        !autoScanEnabled -> "Auto Scan needs a Linear API key for this project."
                        else -> "Scan Linear for matching issues and launch sessions."
                    },
                )
            },
            trailingContent = { ModeChevron() },
            colors = listColorsFor(autoScanEnabled),
        )
    }

    Card(
        onClick = { onChooseMode(CreateSessionMode.CustomShell) },
        enabled = !submitting,
        colors = if (selectedMode == CreateSessionMode.CustomShell) selectedColors else CardDefaults.cardColors(),
        border = BorderStroke(
            2.dp,
            if (selectedMode == CreateSessionMode.CustomShell) MaterialTheme.pixel.brand2
            else MaterialTheme.pixel.surface2,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .testTag("mode_card_terminal"),
    ) {
        ListItem(
            headlineContent = { Text("Terminal") },
            supportingContent = { Text("Bash / cmd in the chosen directory") },
            leadingContent = {
                Icon(
                    imageVector = Icons.Filled.Terminal,
                    contentDescription = null,
                )
            },
            trailingContent = { ModeChevron() },
            colors = listColorsFor(true),
        )
    }

    customTypes.forEach { type ->
        key(type.id) {
            CustomAgentTypeCard(
                type = type,
                selected = selectedMode == CreateSessionMode.Custom(type.id),
                selectedColors = selectedColors,
                listColors = listColorsFor(true),
                onPick = { onChooseMode(CreateSessionMode.Custom(type.id)) },
                onEdit = { onEditCustomType(type) },
                onDelete = { onDeleteCustomType(type) },
            )
        }
    }

    // Always enabled — the 20-type cap is not gated client-side (the cached
    // list can be stale); the editor surfaces the server's 409 inline instead.
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

    // Terminal and custom agent types on a non-git target launch straight from
    // this step, so it needs its own progress affordance — the config steps'
    // "Creating…" button never renders on that path.
    if (submitting) {
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
) {
    var menuExpanded by remember { mutableStateOf(false) }
    Box(modifier = Modifier.fillMaxWidth()) {
        Card(
            colors = if (selected) selectedColors else CardDefaults.cardColors(),
            border = BorderStroke(
                2.dp,
                if (selected) MaterialTheme.pixel.brand2 else MaterialTheme.pixel.surface2,
            ),
            modifier = Modifier
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
) {
    ClaudeOptionControls(
        model = state.claudeModel,
        effort = state.claudeEffort,
        fastMode = state.claudeFastMode,
        ultracode = state.claudeUltracode,
        debug = state.claudeDebug,
        appendSystemPrompt = state.claudeAppendSystemPrompt,
        onModelSelect = viewModel::setClaudeModel,
        onEffortSelect = viewModel::setClaudeEffort,
        onFastModeToggle = viewModel::toggleClaudeFastMode,
        onUltracodeToggle = viewModel::toggleClaudeUltracode,
        onDebugToggle = viewModel::toggleClaudeDebug,
        onAppendSystemPromptChange = viewModel::setClaudeAppendSystemPrompt,
    )
    ToggleRow("Skip permissions", state.skipPermissions, viewModel::toggleSkipPermissions)
    ToggleRow("No memory", state.noMemory, viewModel::toggleNoMemory)

    if (showWorktreeToggle) {
        ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

        if (state.worktree) {
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
        }
    }

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submit,
        enabled = state.selection !is ProjectSelection.None && !state.submitting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CodexConfigSection(
    state: CreateSessionUiState,
    showWorktreeToggle: Boolean,
    viewModel: CreateSessionViewModel,
) {
    val preset = state.codexPreset

    Text("Autonomy", style = MaterialTheme.typography.labelLarge)
    val presets = listOf(
        CodexAutonomyPreset.ReadOnly to "Read-only",
        CodexAutonomyPreset.Auto to "Auto",
        CodexAutonomyPreset.FullAccess to "Full-access",
    )
    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
        presets.forEachIndexed { index, (value, label) ->
            SegmentedButton(
                selected = preset == value,
                onClick = { viewModel.selectCodexPreset(value) },
                shape = RectangleShape,
                colors = pixelSegmentedButtonColors(),
                icon = {},
            ) { Text(label) }
        }
    }
    Text(
        text = when (preset) {
            CodexAutonomyPreset.ReadOnly -> "Reads the project; asks before any change."
            CodexAutonomyPreset.Auto -> "Edits within the workspace; asks before risky actions."
            CodexAutonomyPreset.FullAccess -> "No sandbox or approval prompts (--yolo)."
            null -> "Custom sandbox/approval set in Advanced."
        },
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )

    CliModelDropdown(
        models = state.codexModels?.takeIf { it.isNotEmpty() }
            ?: CreateSessionViewModel.CODEX_MODELS,
        selected = state.codexModel,
        defaultLabel = "Default",
        onSelect = viewModel::setCodexModel,
    )

    // A dropdown, not the old 4-button segmented row: the agent-advertised
    // per-model sets run to 6 levels (low…ultra), which segments would truncate
    // on phone width.
    CodexEffortDropdown(
        state = state,
        onSelect = viewModel::setCodexReasoningEffort,
    )

    var advancedExpanded by remember { mutableStateOf(false) }
    PixelTextButton(
        onClick = { advancedExpanded = !advancedExpanded },
        modifier = Modifier.fillMaxWidth(),
    ) {
        Icon(
            imageVector = if (advancedExpanded) Icons.Filled.KeyboardArrowUp else Icons.Filled.KeyboardArrowDown,
            contentDescription = null,
        )
        Text("Advanced")
    }
    if (advancedExpanded) {
        val fullAuto = state.codexFullAuto
        Text("Sandbox (-s)", style = MaterialTheme.typography.labelLarge)
        val sandboxes = listOf(
            "read-only" to "Read-only",
            "workspace-write" to "Workspace",
            "danger-full-access" to "Full",
        )
        SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
            sandboxes.forEachIndexed { index, (value, label) ->
                SegmentedButton(
                    selected = state.codexSandbox == value,
                    onClick = { viewModel.setCodexSandbox(value) },
                    enabled = !fullAuto,
                    shape = RectangleShape,
                    colors = pixelSegmentedButtonColors(),
                    icon = {},
                ) { Text(label) }
            }
        }
        Text("Approval (-a)", style = MaterialTheme.typography.labelLarge)
        val approvals = listOf(
            "untrusted" to "Untrusted",
            "on-request" to "On-request",
            "never" to "Never",
        )
        SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
            approvals.forEachIndexed { index, (value, label) ->
                SegmentedButton(
                    selected = state.codexApproval == value,
                    onClick = { viewModel.setCodexApproval(value) },
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

    if (showWorktreeToggle) {
        ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

        if (state.worktree) {
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
        }
    }

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitCodex,
        enabled = state.selection !is ProjectSelection.None && !state.submitting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
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
) {
    CliModelDropdown(
        models = state.antigravityModels?.takeIf { it.isNotEmpty() }
            ?: CreateSessionViewModel.ANTIGRAVITY_MODELS,
        selected = state.antigravityModel,
        defaultLabel = "Auto",
        onSelect = viewModel::setAntigravityModel,
    )

    Text("Permissions", style = MaterialTheme.typography.labelLarge)
    ToggleRow(
        label = "Skip permissions (dangerous)",
        checked = state.antigravitySkipPermissions,
        onChange = viewModel::setAntigravitySkipPermissions,
        enabled = !state.antigravitySandbox,
    )
    ToggleRow(
        label = "Sandbox",
        checked = state.antigravitySandbox,
        onChange = viewModel::setAntigravitySandbox,
        enabled = !state.antigravitySkipPermissions,
    )
    Text(
        text = when {
            state.antigravitySkipPermissions ->
                "Auto-approves every tool permission request (--dangerously-skip-permissions)."
            state.antigravitySandbox ->
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
        value = state.antigravityAddDirsDraft,
        onValueChange = viewModel::setAntigravityAddDirsDraft,
        label = { Text("Additional directories (one per line, optional)") },
        supportingText = { Text("Each is added to the workspace via --add-dir.") },
        keyboardOptions = KeyboardOptions(
            capitalization = KeyboardCapitalization.None,
            autoCorrectEnabled = false,
        ),
        modifier = Modifier.fillMaxWidth(),
    )

    if (showWorktreeToggle) {
        ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

        if (state.worktree) {
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
        }
    }

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitAntigravity,
        enabled = state.selection !is ProjectSelection.None && !state.submitting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
    }
}

/**
 * Claude reasoning controls shared by the Standard and Auto Scan config steps:
 * a model dropdown, a model-conditional effort dropdown, and an "Advanced"
 * expander (fast mode, ultracode, debug logging, append-system-prompt).
 * Replaces the old boolean "Max effort" toggle.
 */
@Composable
private fun ClaudeOptionControls(
    model: String?,
    effort: String,
    fastMode: Boolean,
    ultracode: Boolean,
    debug: Boolean,
    appendSystemPrompt: String,
    onModelSelect: (String?) -> Unit,
    onEffortSelect: (String) -> Unit,
    onFastModeToggle: (Boolean) -> Unit,
    onUltracodeToggle: (Boolean) -> Unit,
    onDebugToggle: (Boolean) -> Unit,
    onAppendSystemPromptChange: (String) -> Unit,
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
        enabled = !ultracode,
        onSelect = onEffortSelect,
    )
    if (ultracode) {
        Text(
            text = "ultracode runs at xhigh.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }

    var advancedExpanded by remember { mutableStateOf(false) }
    PixelTextButton(
        onClick = { advancedExpanded = !advancedExpanded },
        modifier = Modifier.fillMaxWidth(),
    ) {
        Icon(
            imageVector = if (advancedExpanded) Icons.Filled.KeyboardArrowUp else Icons.Filled.KeyboardArrowDown,
            contentDescription = null,
        )
        Text("Advanced")
    }
    if (advancedExpanded) {
        ToggleRow("Fast mode (Opus only)", fastMode, onFastModeToggle)
        ToggleRow("ultracode", ultracode, onUltracodeToggle)
        ToggleRow("Debug logging", debug, onDebugToggle)
        Text(
            text = "Claude Code prints verbose diagnostics into the terminal.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
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
    enabled: Boolean,
    onSelect: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val options = CreateSessionViewModel.claudeEffortsFor(model)
    ExposedDropdownMenuBox(
        expanded = expanded && enabled,
        onExpandedChange = { if (enabled) expanded = !expanded },
    ) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = selected.replaceFirstChar { it.uppercase() },
            onValueChange = {},
            readOnly = true,
            enabled = enabled,
            label = { Text("Effort") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        DropdownMenu(
            expanded = expanded && enabled,
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
    state: CreateSessionUiState,
    onSelect: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val advertised = state.codexEffortsFor(state.codexModel)
    // Keep-selected repair: a restored-prefs or late-probe effort outside the
    // advertised set must stay in the menu, or the field would display a value
    // its own options cannot reproduce while the request still sends it.
    val options = if (state.codexReasoningEffort in advertised) advertised
    else advertised + state.codexReasoningEffort
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = !expanded },
    ) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = state.codexReasoningEffort.replaceFirstChar { it.uppercase() },
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

@Composable
private fun TerminalConfigSection(
    state: CreateSessionUiState,
    viewModel: CreateSessionViewModel,
) {
    ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

    if (state.worktree) {
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
    }

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitFromTerminalConfig,
        enabled = state.selection !is ProjectSelection.None && !state.submitting,
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
    ClaudeOptionControls(
        model = state.autoScanClaudeModel,
        effort = state.autoScanClaudeEffort,
        fastMode = state.autoScanClaudeFastMode,
        ultracode = state.autoScanClaudeUltracode,
        debug = state.autoScanClaudeDebug,
        appendSystemPrompt = state.autoScanClaudeAppendSystemPrompt,
        onModelSelect = viewModel::setAutoScanClaudeModel,
        onEffortSelect = viewModel::setAutoScanClaudeEffort,
        onFastModeToggle = viewModel::toggleAutoScanClaudeFastMode,
        onUltracodeToggle = viewModel::toggleAutoScanClaudeUltracode,
        onDebugToggle = viewModel::toggleAutoScanClaudeDebug,
        onAppendSystemPromptChange = viewModel::setAutoScanClaudeAppendSystemPrompt,
    )
    ToggleRow("Plan mode", state.autoScanPlanMode, viewModel::toggleAutoScanPlanMode)
    ToggleRow(
        "Skip permissions",
        state.autoScanSkipPermissions,
        viewModel::toggleAutoScanSkipPermissions,
    )
    Text(
        text = "When off, the scan pauses at permission prompts — you'll get a " +
            "notification and can open the session to approve.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    ToggleRow("No memory", state.autoScanNoMemory, viewModel::toggleAutoScanNoMemory)

    OutlinedTextField(
        colors = PixelTextFieldDefaults.colors(),
        value = state.initialPrompt,
        onValueChange = viewModel::setInitialPrompt,
        label = { Text("Initial prompt (optional)") },
        modifier = Modifier.fillMaxWidth(),
        minLines = 3,
        maxLines = 8,
        keyboardOptions = KeyboardOptions(
            capitalization = KeyboardCapitalization.None,
            autoCorrectEnabled = false,
        ),
    )

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitAutoScan,
        enabled = state.selectedProjectName != null && !state.autoScanStarting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.autoScanStarting) "Starting…" else "Start Auto Scan")
    }
}

@Composable
private fun ToggleRow(
    label: String,
    checked: Boolean,
    onChange: (Boolean) -> Unit,
    enabled: Boolean = true,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
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
            onCheckedChange = onChange,
            enabled = enabled,
            colors = pixelSwitchColors(),
        )
    }
}
