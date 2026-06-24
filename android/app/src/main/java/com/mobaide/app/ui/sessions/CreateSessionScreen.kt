package com.mobaide.app.ui.sessions

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
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

    // System back navigates between steps before exiting the screen.
    BackHandler(enabled = state.step != CreateSessionStep.ProjectSelect) {
        viewModel.goBack()
    }

    val onTopBarBack: () -> Unit = {
        if (state.step == CreateSessionStep.ProjectSelect) onBack() else viewModel.goBack()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        when (state.step) {
                            CreateSessionStep.ProjectSelect -> "New Session"
                            CreateSessionStep.ModePick -> "Select mode"
                            CreateSessionStep.StandardConfig -> "Claude code session"
                            CreateSessionStep.CodexConfig -> "Codex session"
                            CreateSessionStep.AutoScanConfig -> "Auto Scan mode"
                            CreateSessionStep.TerminalConfig -> "Terminal session"
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
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
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
                    showAutoScan = storedProject?.has_linear_key == true,
                )
                is ProjectSelection.CustomPath -> ModeHeader(
                    title = "Custom path",
                    subtitle = sel.path,
                    showAutoScan = false,
                )
                ProjectSelection.None -> ModeHeader(
                    title = "",
                    subtitle = null,
                    showAutoScan = false,
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
                    onCreateProjectClicked = projectsViewModel::openCreateSheet,
                    onContinue = viewModel::confirmProject,
                )
                CreateSessionStep.ModePick -> ModePickerSection(
                    header = modeHeader,
                    selectedMode = state.selectedMode,
                    onPickMode = viewModel::pickMode,
                    onCommit = {
                        when (state.selectedMode) {
                            CreateSessionMode.Standard -> viewModel.selectStandardMode()
                            CreateSessionMode.Codex -> viewModel.selectCodexMode()
                            CreateSessionMode.AutoScan -> viewModel.selectAutoScanMode()
                            CreateSessionMode.CustomShell -> viewModel.selectTerminalMode()
                            null -> Unit
                        }
                    },
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
    AlertDialog(
        onDismissRequest = onDismiss,
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
            Button(onClick = onSubmit, enabled = apiKey.isNotBlank() && !submitting) {
                Text(if (submitting) "Signing in…" else "Sign in")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, enabled = !submitting) {
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
                HorizontalDivider()
            }
            DropdownMenuItem(
                text = {
                    Column {
                        Text("Custom path…")
                        Text(
                            text = "Run once, don't save",
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
        )
    }

    val canContinue = when (val sel = state.selection) {
        is ProjectSelection.Stored -> true
        is ProjectSelection.CustomPath ->
            state.customPathDraft.isNotBlank() && state.customPathError == null
        ProjectSelection.None -> false
    }
    Button(
        onClick = onContinue,
        enabled = canContinue,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text("Continue")
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CustomPathPanel(
    state: CreateSessionUiState,
    onDraftChange: (String) -> Unit,
    onPickRecent: (String) -> Unit,
) {
    OutlinedTextField(
        value = state.customPathDraft,
        onValueChange = onDraftChange,
        label = { Text("Absolute path on the agent") },
        singleLine = true,
        isError = state.customPathError != null,
        supportingText = {
            Text(
                text = state.customPathError
                    ?: "e.g. /home/user/code or D:\\projects\\demo",
            )
        },
        modifier = Modifier.fillMaxWidth(),
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
                AssistChip(
                    onClick = { onPickRecent(path) },
                    label = { Text(path) },
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
    val showAutoScan: Boolean,
)

@Composable
private fun ModePickerSection(
    header: ModeHeader,
    selectedMode: CreateSessionMode?,
    onPickMode: (CreateSessionMode) -> Unit,
    onCommit: () -> Unit,
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

    val selectedColors = CardDefaults.elevatedCardColors(
        containerColor = MaterialTheme.colorScheme.secondaryContainer,
    )

    ElevatedCard(
        onClick = { onPickMode(CreateSessionMode.Standard) },
        colors = if (selectedMode == CreateSessionMode.Standard) selectedColors else CardDefaults.elevatedCardColors(),
        modifier = Modifier.fillMaxWidth(),
    ) {
        ListItem(
            headlineContent = { Text("Claude code session") },
            supportingContent = { Text("Configure and launch a Claude session.") },
            colors = androidx.compose.material3.ListItemDefaults.colors(containerColor = androidx.compose.ui.graphics.Color.Transparent),
        )
    }

    ElevatedCard(
        onClick = { onPickMode(CreateSessionMode.Codex) },
        colors = if (selectedMode == CreateSessionMode.Codex) selectedColors else CardDefaults.elevatedCardColors(),
        modifier = Modifier.fillMaxWidth(),
    ) {
        ListItem(
            headlineContent = { Text("Codex session") },
            supportingContent = { Text("Configure and launch an OpenAI Codex session.") },
            colors = androidx.compose.material3.ListItemDefaults.colors(containerColor = androidx.compose.ui.graphics.Color.Transparent),
        )
    }

    if (header.showAutoScan) {
        ElevatedCard(
            onClick = { onPickMode(CreateSessionMode.AutoScan) },
            colors = if (selectedMode == CreateSessionMode.AutoScan) selectedColors else CardDefaults.elevatedCardColors(),
            modifier = Modifier.fillMaxWidth(),
        ) {
            ListItem(
                headlineContent = { Text("Auto Scan mode") },
                supportingContent = { Text("Scan Linear for AutoPipeline issues and launch sessions.") },
                colors = androidx.compose.material3.ListItemDefaults.colors(containerColor = androidx.compose.ui.graphics.Color.Transparent),
            )
        }
    }

    ElevatedCard(
        onClick = { onPickMode(CreateSessionMode.CustomShell) },
        colors = if (selectedMode == CreateSessionMode.CustomShell) selectedColors else CardDefaults.elevatedCardColors(),
        modifier = Modifier.fillMaxWidth(),
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
            colors = androidx.compose.material3.ListItemDefaults.colors(containerColor = androidx.compose.ui.graphics.Color.Transparent),
        )
    }

    Button(
        onClick = onCommit,
        enabled = selectedMode != null,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text("Continue")
    }
}

@Composable
private fun StandardConfigSection(
    state: CreateSessionUiState,
    showWorktreeToggle: Boolean,
    viewModel: CreateSessionViewModel,
) {
    ToggleRow("Max effort", state.maxEffort, viewModel::toggleMaxEffort)
    ToggleRow("Skip permissions", state.skipPermissions, viewModel::toggleSkipPermissions)
    ToggleRow("No memory", state.noMemory, viewModel::toggleNoMemory)

    if (showWorktreeToggle) {
        ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

        if (state.worktree) {
            OutlinedTextField(
                value = state.branchName,
                onValueChange = viewModel::setBranchName,
                label = { Text("Branch name (optional)") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }

    Button(
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
                shape = SegmentedButtonDefaults.itemShape(index = index, count = presets.size),
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

    Text("Model", style = MaterialTheme.typography.labelLarge)
    CodexModelDropdown(selected = state.codexModel, onSelect = viewModel::setCodexModel)

    Text("Reasoning effort", style = MaterialTheme.typography.labelLarge)
    val efforts = CreateSessionViewModel.REASONING_EFFORTS
    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
        efforts.forEachIndexed { index, effort ->
            SegmentedButton(
                selected = state.codexReasoningEffort == effort,
                onClick = { viewModel.setCodexReasoningEffort(effort) },
                shape = SegmentedButtonDefaults.itemShape(index = index, count = efforts.size),
                icon = {},
            ) { Text(effort.replaceFirstChar { it.uppercase() }) }
        }
    }

    var advancedExpanded by remember { mutableStateOf(false) }
    TextButton(
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
                    shape = SegmentedButtonDefaults.itemShape(index = index, count = sandboxes.size),
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
                    shape = SegmentedButtonDefaults.itemShape(index = index, count = approvals.size),
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
                value = state.branchName,
                onValueChange = viewModel::setBranchName,
                label = { Text("Branch name (optional)") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }

    Button(
        onClick = viewModel::submitCodex,
        enabled = state.selection !is ProjectSelection.None && !state.submitting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.submitting) "Creating…" else "Create session")
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CodexModelDropdown(
    selected: String?,
    onSelect: (String?) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = !expanded },
    ) {
        OutlinedTextField(
            value = selected ?: "Default",
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
                text = { Text("Default") },
                onClick = {
                    onSelect(null)
                    expanded = false
                },
            )
            CreateSessionViewModel.CODEX_MODELS.forEach { model ->
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

@Composable
private fun TerminalConfigSection(
    state: CreateSessionUiState,
    viewModel: CreateSessionViewModel,
) {
    ToggleRow("Create worktree", state.worktree, viewModel::toggleWorktree)

    if (state.worktree) {
        OutlinedTextField(
            value = state.branchName,
            onValueChange = viewModel::setBranchName,
            label = { Text("Branch name (optional)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
    }

    Button(
        onClick = viewModel::submitCustomShell,
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
    ToggleRow("Max effort", state.maxEffort, viewModel::toggleMaxEffort)
    ToggleRow("Plan mode", state.planMode, viewModel::togglePlanMode)
    ToggleRow("Skip permissions", state.skipPermissions, viewModel::toggleSkipPermissions)
    ToggleRow("No memory", state.noMemory, viewModel::toggleNoMemory)

    OutlinedTextField(
        value = state.initialPrompt,
        onValueChange = viewModel::setInitialPrompt,
        label = { Text("Initial prompt (optional)") },
        modifier = Modifier.fillMaxWidth(),
        minLines = 3,
        maxLines = 8,
    )

    Button(
        onClick = viewModel::submitAutoScan,
        enabled = state.selectedProjectName != null && !state.autoScanStarting,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(if (state.autoScanStarting) "Starting…" else "Start Auto Scan")
    }
}

@Composable
private fun ToggleRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text = label, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange)
    }
}
