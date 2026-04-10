package com.claudemobile.app.ui.sessions

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.Button
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
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

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CreateSessionScreen(
    viewModel: CreateSessionViewModel,
    onBack: () -> Unit,
    onSessionCreated: (String) -> Unit,
    onAutoScanStarted: (String) -> Unit,
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
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
                            CreateSessionStep.StandardConfig -> "Standard mode"
                            CreateSessionStep.AutoScanConfig -> "Auto Scan mode"
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
            val selectedProject = state.projects.firstOrNull { it.name == state.selectedProjectName }

            when (state.step) {
                CreateSessionStep.ProjectSelect -> ProjectSelectSection(
                    state = state,
                    selectedProject = selectedProject,
                    onSelect = viewModel::selectProject,
                    onContinue = viewModel::confirmProject,
                )
                CreateSessionStep.ModePick -> ModePickerSection(
                    project = selectedProject,
                    selectedMode = state.selectedMode,
                    onPickMode = viewModel::pickMode,
                    onCommit = {
                        when (state.selectedMode) {
                            CreateSessionMode.Standard -> viewModel.selectStandardMode()
                            CreateSessionMode.AutoScan -> viewModel.selectAutoScanMode()
                            null -> Unit
                        }
                    },
                )
                CreateSessionStep.StandardConfig -> StandardConfigSection(
                    state = state,
                    viewModel = viewModel,
                )
                CreateSessionStep.AutoScanConfig -> AutoScanConfigSection(
                    state = state,
                    viewModel = viewModel,
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ProjectSelectSection(
    state: CreateSessionUiState,
    selectedProject: com.claudemobile.app.data.network.dto.ProjectInfo?,
    onSelect: (String) -> Unit,
    onContinue: () -> Unit,
) {
    var dropdownExpanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = dropdownExpanded,
        onExpandedChange = { dropdownExpanded = !dropdownExpanded },
    ) {
        OutlinedTextField(
            value = state.selectedProjectName ?: if (state.isLoadingProjects) "Loading…" else "Select project",
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

    Button(
        onClick = onContinue,
        enabled = state.selectedProjectName != null,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text("Continue")
    }
}

@Composable
private fun ModePickerSection(
    project: com.claudemobile.app.data.network.dto.ProjectInfo?,
    selectedMode: CreateSessionMode?,
    onPickMode: (CreateSessionMode) -> Unit,
    onCommit: () -> Unit,
) {
    Text(
        text = project?.name ?: "",
        style = MaterialTheme.typography.titleMedium,
    )
    project?.path?.let {
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
            headlineContent = { Text("Standard mode") },
            supportingContent = { Text("Configure and launch a Claude session.") },
            colors = androidx.compose.material3.ListItemDefaults.colors(containerColor = androidx.compose.ui.graphics.Color.Transparent),
        )
    }

    // Per CLA-15 AC: hide Auto Scan entirely when no Linear key is configured.
    if (project?.has_linear_key == true) {
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
    viewModel: CreateSessionViewModel,
) {
    val isGitProject = state.projects
        .firstOrNull { it.name == state.selectedProjectName }
        ?.is_git == true

    ToggleRow("Skip permissions", state.skipPermissions, viewModel::toggleSkipPermissions)
    ToggleRow("No memory", state.noMemory, viewModel::toggleNoMemory)

    // CLA-20: worktree requires a git repo — hide the toggle for non-git projects.
    if (isGitProject) {
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
        enabled = state.selectedProjectName != null && !state.submitting,
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
    val isGitProject = state.projects
        .firstOrNull { it.name == state.selectedProjectName }
        ?.is_git == true

    ToggleRow("Plan mode", state.planMode, viewModel::togglePlanMode)
    ToggleRow("Skip permissions", state.skipPermissions, viewModel::toggleSkipPermissions)
    ToggleRow("No memory", state.noMemory, viewModel::toggleNoMemory)

    if (isGitProject) {
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
