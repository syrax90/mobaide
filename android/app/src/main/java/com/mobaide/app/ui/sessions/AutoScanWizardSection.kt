package com.mobaide.app.ui.sessions

import android.content.res.Configuration
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.InputChip
import androidx.compose.material3.InputChipDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_GITHUB
import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_LINEAR
import com.mobaide.app.data.network.dto.AutoScanCapability
import com.mobaide.app.data.network.dto.GitHubLabelDto
import com.mobaide.app.data.network.dto.GitHubStatusDto
import com.mobaide.app.data.network.dto.WorkspaceInfo
import com.mobaide.app.ui.autoscan.autoScanAgentName
import com.mobaide.app.ui.common.AutoScanLinearFilterEditor
import com.mobaide.app.ui.common.LinearFilterErrors
import com.mobaide.app.ui.common.SecretTextField
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import com.mobaide.app.ui.common.pixel.pixelSegmentedButtonColors
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

private const val GITHUB_LABEL_SUGGESTIONS_SHOWN = 30

/**
 * The Auto Scan step for an agent with `capabilities.autoscan`: the issue
 * source and its filter, the AI agent and its options, and the first prompt.
 */
@Composable
internal fun AutoScanWizardSection(
    state: CreateSessionUiState,
    capability: AutoScanCapability,
    viewModel: CreateSessionViewModel,
) {
    val wizard = state.autoScan
    val workspace = state.selectedWorkspace

    SectionLabel("Issue source")
    AutoScanSourcePicker(
        sources = capability.sources,
        selected = wizard.source,
        onSelect = viewModel::setAutoScanSource,
    )
    if (wizard.source == null) Hint("Choose where to find issues.")

    when (wizard.source) {
        AUTO_SCAN_SOURCE_GITHUB -> GitHubSourcePanel(
            wizard = wizard,
            setupAvailable = capability.githubSetup,
            onCheck = viewModel::refreshGitHubStatus,
            onSetup = viewModel::startGitHubSetup,
            onToggleLabel = viewModel::toggleAutoScanGitHubLabel,
            onLabelDraftChange = viewModel::setAutoScanGitHubLabelDraft,
            onAddLabel = viewModel::addAutoScanGitHubLabelDraft,
            onAssigneeChange = viewModel::setAutoScanGitHubAssignee,
            onActiveLabelChange = viewModel::setAutoScanGitHubActiveLabel,
        )
        AUTO_SCAN_SOURCE_LINEAR -> if (workspace != null) {
            LinearSourcePanel(workspace = workspace, wizard = wizard, viewModel = viewModel)
        }
    }

    HorizontalDivider(thickness = 2.dp)

    SectionLabel("AI agent")
    val cli = state.autoScanCli
    AutoScanAgentDropdown(
        clis = capability.clis,
        selected = cli,
        isAvailable = { state.cliAvailability(it) == true },
        onSelect = viewModel::setAutoScanCli,
    )
    when (cli) {
        null -> Unit
        "claude" -> AutoScanClaudeOptions(state = state, viewModel = viewModel)
        "pi" -> Hint("Pi runs its tools without asking.")
        else -> {
            ToggleRow(
                label = "Run without asking for permission (dangerous)",
                checked = wizard.autoApprove,
                onChange = viewModel::setAutoScanAutoApprove,
            )
            Hint("When on, the agent runs commands without asking (Codex also without its sandbox). When off, it may stop and wait for your approval in its session; Codex never asks and stays in its sandbox.")
        }
    }

    HorizontalDivider(thickness = 2.dp)

    AutoScanInitialPromptField(state.initialPrompt, viewModel::setInitialPrompt)
    Hint("The issue's title, link and description are added after your prompt.")

    wizard.startError?.let { ErrorText(it, Modifier.testTag("autoscan_start_error")) }
    val blocker = state.autoScanStartBlocker
    if (blocker != null) Hint(blocker, Modifier.testTag("autoscan_start_blocker"))

    PixelButton(
        style = PixelButtonStyle.Primary,
        onClick = viewModel::submitAutoScan,
        enabled = blocker == null && !state.autoScanStarting,
        modifier = Modifier
            .fillMaxWidth()
            .testTag("autoscan_start"),
    ) {
        Text(if (state.autoScanStarting) "Starting…" else "Start Auto Scan")
    }
}

@Composable
private fun AutoScanSourcePicker(
    sources: List<String>,
    selected: String?,
    onSelect: (String) -> Unit,
) {
    val options = listOf(
        AUTO_SCAN_SOURCE_GITHUB to "GitHub Issues",
        AUTO_SCAN_SOURCE_LINEAR to "Linear",
    ).filter { (source, _) -> source in sources }
    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
        options.forEach { (source, label) ->
            SegmentedButton(
                selected = selected == source,
                onClick = { onSelect(source) },
                shape = RectangleShape,
                colors = pixelSegmentedButtonColors(),
                icon = {},
                modifier = Modifier.testTag("autoscan_source_$source"),
            ) { Text(label) }
        }
    }
}

@Composable
private fun GitHubSourcePanel(
    wizard: AutoScanWizardState,
    setupAvailable: Boolean,
    onCheck: (Boolean) -> Unit,
    onSetup: (String) -> Unit,
    onToggleLabel: (String) -> Unit,
    onLabelDraftChange: (String) -> Unit,
    onAddLabel: () -> Unit,
    onAssigneeChange: (String) -> Unit,
    onActiveLabelChange: (String) -> Unit,
) {
    // Fires when the panel first shows and on every return to this screen:
    // back from the setup terminal, or the app back in the foreground.
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { onCheck(false) }

    GitHubStatusBlock(
        status = wizard.githubStatus,
        loading = wizard.githubStatusLoading,
        error = wizard.githubStatusError,
        setupAvailable = setupAvailable,
        setupStarting = wizard.githubSetupStarting,
        setupError = wizard.githubSetupError,
        onCheckAgain = { onCheck(true) },
        onSetup = onSetup,
    )

    GitHubLabelsField(
        selected = wizard.githubFilter.labels,
        repoLabels = wizard.githubLabels,
        loading = wizard.githubLabelsLoading,
        loadError = wizard.githubLabelsError,
        draft = wizard.githubLabelDraft,
        draftError = wizard.githubLabelDraftError,
        onToggle = onToggleLabel,
        onDraftChange = onLabelDraftChange,
        onAdd = onAddLabel,
    )

    val assigneeError = wizard.githubFilter.assigneeError
    OutlinedTextField(
        colors = PixelTextFieldDefaults.colors(),
        value = wizard.githubFilter.assignee,
        onValueChange = onAssigneeChange,
        label = { Text("Assigned to") },
        placeholder = { Text("Anyone") },
        singleLine = true,
        isError = assigneeError != null,
        supportingText = {
            Text(assigneeError ?: "Empty = any assignee. @me = the signed-in GitHub account.")
        },
        keyboardOptions = KeyboardOptions(
            capitalization = KeyboardCapitalization.None,
            autoCorrectEnabled = false,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .testTag("autoscan_github_assignee"),
    )

    val activeLabelError = wizard.githubFilter.activeLabelError
    OutlinedTextField(
        colors = PixelTextFieldDefaults.colors(),
        value = wizard.githubFilter.activeLabel,
        onValueChange = onActiveLabelChange,
        label = { Text("Label to add when work starts") },
        placeholder = { Text("None") },
        singleLine = true,
        isError = activeLabelError != null,
        supportingText = {
            Text(activeLabelError ?: "Optional, for example in-progress. It must exist in the repository.")
        },
        keyboardOptions = KeyboardOptions(
            capitalization = KeyboardCapitalization.None,
            autoCorrectEnabled = false,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .testTag("autoscan_github_active_label"),
    )

    Hint("When an issue is closed, its worktree is removed.")
}

/** What the PC still needs for a GitHub scan, with the buttons that fix it. */
@Composable
private fun GitHubStatusBlock(
    status: GitHubStatusDto?,
    loading: Boolean,
    error: String?,
    setupAvailable: Boolean,
    setupStarting: Boolean,
    setupError: String?,
    onCheckAgain: () -> Unit,
    onSetup: (String) -> Unit,
) {
    Card(
        colors = CardDefaults.cardColors(),
        border = BorderStroke(2.dp, MaterialTheme.pixel.surface2),
        modifier = Modifier
            .fillMaxWidth()
            .testTag("autoscan_github_status"),
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (loading) LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            when {
                status == null && error != null -> ErrorText(error)
                status == null -> Text("Checking GitHub on the PC…")
                !status.installed -> {
                    Text(status.problem ?: "GitHub CLI (gh) is not installed on the PC.")
                    if (status.can_install && setupAvailable) {
                        PixelButton(
                            style = PixelButtonStyle.Primary,
                            onClick = { onSetup("install") },
                            enabled = !setupStarting,
                            modifier = Modifier.testTag("autoscan_github_install"),
                        ) { Text("Install GitHub CLI") }
                        status.install_command?.let { Hint("Runs on the PC: $it") }
                    } else {
                        Hint("Install it from cli.github.com, then tap Check again.")
                    }
                }
                // The agent sets `host` for any remote; only a GitHub host can be signed in to.
                !status.signed_in && status.host?.contains("github") == true -> {
                    Text(status.problem ?: "GitHub CLI is not signed in on the PC.")
                    if (setupAvailable) {
                        PixelButton(
                            style = PixelButtonStyle.Primary,
                            onClick = { onSetup("sign_in") },
                            enabled = !setupStarting,
                            modifier = Modifier.testTag("autoscan_github_sign_in"),
                        ) { Text("Sign in to GitHub") }
                    } else {
                        Hint("Run gh auth login on the PC, then tap Check again.")
                    }
                }
                status.problem != null -> Text(status.problem)
                else -> Text(
                    listOfNotNull(
                        status.repo?.let { "Repository $it" },
                        status.account?.let { "Signed in as $it" },
                    ).joinToString(" · ").ifEmpty { "GitHub is ready." },
                )
            }
            if (status != null && error != null) ErrorText(error)
            setupError?.let { ErrorText(it) }
            PixelTextButton(
                onClick = onCheckAgain,
                enabled = !loading,
                modifier = Modifier.testTag("autoscan_github_check_again"),
            ) { Text("Check again") }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun GitHubLabelsField(
    selected: List<String>,
    repoLabels: List<GitHubLabelDto>,
    loading: Boolean,
    loadError: String?,
    draft: String,
    draftError: String?,
    onToggle: (String) -> Unit,
    onDraftChange: (String) -> Unit,
    onAdd: () -> Unit,
) {
    SectionLabel("Labels to pick up")
    Hint(
        "An open issue with any of these labels is picked up. The scan removes the label " +
            "when it starts, so each issue runs once.",
    )
    if (selected.isNotEmpty()) {
        FlowRow(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("autoscan_github_labels"),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            selected.forEach { name ->
                InputChip(
                    selected = true,
                    onClick = { onToggle(name) },
                    label = { Text(name) },
                    trailingIcon = {
                        Icon(
                            Icons.Filled.Close,
                            contentDescription = "Remove $name",
                            modifier = Modifier.size(InputChipDefaults.AvatarSize),
                        )
                    },
                )
            }
        }
    }
    OutlinedTextField(
        colors = PixelTextFieldDefaults.colors(),
        value = draft,
        onValueChange = onDraftChange,
        label = { Text("Add a label") },
        singleLine = true,
        isError = draftError != null,
        supportingText = { Text(draftError ?: "Type a label name, or pick one below.") },
        trailingIcon = {
            IconButton(onClick = onAdd, enabled = draft.isNotBlank()) {
                Icon(Icons.Filled.Add, contentDescription = "Add label")
            }
        },
        keyboardOptions = KeyboardOptions(
            capitalization = KeyboardCapitalization.None,
            autoCorrectEnabled = false,
            imeAction = ImeAction.Done,
        ),
        keyboardActions = KeyboardActions(onDone = { onAdd() }),
        modifier = Modifier
            .fillMaxWidth()
            .testTag("autoscan_github_label_input"),
    )
    if (loading) LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
    loadError?.let { Hint(it) }
    val query = draft.trim()
    val suggestions = repoLabels.map { it.name }.filter { name ->
        name !in selected && (query.isEmpty() || name.contains(query, ignoreCase = true))
    }
    if (suggestions.isNotEmpty()) {
        FlowRow(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("autoscan_github_label_suggestions"),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            suggestions.take(GITHUB_LABEL_SUGGESTIONS_SHOWN).forEach { name ->
                FilterChip(
                    selected = false,
                    onClick = { onToggle(name) },
                    label = { Text(name) },
                )
            }
        }
        val hidden = suggestions.size - GITHUB_LABEL_SUGGESTIONS_SHOWN
        if (hidden > 0) Hint("$hidden more ${if (hidden == 1) "label" else "labels"}: type to find them.")
    }
}

@Composable
private fun LinearSourcePanel(
    workspace: WorkspaceInfo,
    wizard: AutoScanWizardState,
    viewModel: CreateSessionViewModel,
) {
    if (!workspace.has_linear_key) {
        Hint("Auto Scan reads Linear with the API key of this workspace.")
        SecretTextField(
            value = wizard.linearKeyDraft,
            onValueChange = viewModel::setAutoScanLinearKeyDraft,
            label = "Linear API key",
            revealed = wizard.linearKeyVisible,
            onToggleRevealed = viewModel::toggleAutoScanLinearKeyVisible,
            enabled = !wizard.linearKeySaving,
            imeAction = ImeAction.Done,
            keyboardActions = KeyboardActions(onDone = { viewModel.saveAutoScanLinearKey() }),
            modifier = Modifier
                .fillMaxWidth()
                .testTag("autoscan_linear_key"),
        )
        wizard.linearKeyError?.let { ErrorText(it) }
        PixelButton(
            onClick = viewModel::saveAutoScanLinearKey,
            enabled = wizard.linearKeyDraft.isNotBlank() && !wizard.linearKeySaving,
            modifier = Modifier.testTag("autoscan_linear_key_save"),
        ) { Text(if (wizard.linearKeySaving) "Saving…" else "Save key") }
        return
    }
    LaunchedEffect(workspace.name) { viewModel.loadLinearMetadata() }
    AutoScanLinearFilterEditor(
        availableLabels = wizard.linearLabels,
        availableStates = wizard.linearStates,
        triggerLabels = wizard.linearFilter.triggerLabels,
        triggerStates = wizard.linearFilter.triggerStates,
        activeState = wizard.linearFilter.activeState,
        doneState = wizard.linearFilter.doneState,
        removalLabel = wizard.linearFilter.removalLabel,
        loading = wizard.linearMetadataLoading,
        loadError = wizard.linearMetadataError,
        errors = if (wizard.linearFilterTouched) wizard.linearFilter.errors else LinearFilterErrors(),
        onToggleTriggerLabel = viewModel::toggleAutoScanLinearLabel,
        onToggleTriggerState = viewModel::toggleAutoScanLinearState,
        onActiveStateChange = viewModel::setAutoScanLinearActiveState,
        onDoneStateChange = viewModel::setAutoScanLinearDoneState,
        onRemovalLabelChange = viewModel::setAutoScanLinearRemovalLabel,
        onRefresh = { viewModel.loadLinearMetadata(force = true) },
        title = "Linear filter",
        description = "Each matching issue runs in its own git worktree, so its changes stay " +
            "apart from your working copy. When an issue reaches the done state, its worktree is removed.",
    )
}

/** The agents Auto Scan can run; an agent not installed on the PC shows but cannot be chosen. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AutoScanAgentDropdown(
    clis: List<String>,
    selected: String?,
    isAvailable: (String) -> Boolean,
    onSelect: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val selectedMissing = selected != null && !isAvailable(selected)
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = !expanded },
    ) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = selected?.let(::autoScanAgentName).orEmpty(),
            onValueChange = {},
            readOnly = true,
            label = { Text("Agent") },
            placeholder = { Text("Choose an agent") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            isError = selectedMissing,
            supportingText = if (selectedMissing) {
                { Text("Not installed on the PC") }
            } else {
                null
            },
            modifier = Modifier
                .menuAnchor()
                .fillMaxWidth()
                .testTag("autoscan_agent"),
        )
        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            clis.forEach { cli ->
                val available = isAvailable(cli)
                DropdownMenuItem(
                    text = {
                        Column {
                            Text(autoScanAgentName(cli))
                            if (!available) {
                                Text(
                                    text = "Not installed on the PC",
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        }
                    },
                    onClick = {
                        onSelect(cli)
                        expanded = false
                    },
                    enabled = available,
                    modifier = Modifier.testTag("autoscan_agent_$cli"),
                )
            }
        }
    }
}

@Composable
private fun SectionLabel(text: String) {
    Text(text = text, style = MaterialTheme.typography.labelLarge)
}

@Composable
private fun Hint(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = modifier,
    )
}

@Composable
private fun ErrorText(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.error,
        modifier = modifier,
    )
}

@Preview(name = "GitHubStatusBlock light", showBackground = true)
@Preview(
    name = "GitHubStatusBlock dark",
    showBackground = true,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun GitHubStatusBlockPreview() {
    MobaideTheme {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            GitHubStatusBlock(
                status = GitHubStatusDto(
                    installed = false,
                    problem = "GitHub CLI (gh) is not installed on the PC.",
                    can_install = true,
                    install_command = "brew install gh",
                ),
                loading = false,
                error = null,
                setupAvailable = true,
                setupStarting = false,
                setupError = null,
                onCheckAgain = {},
                onSetup = {},
            )
            GitHubStatusBlock(
                status = GitHubStatusDto(
                    installed = true,
                    signed_in = true,
                    account = "octocat",
                    repo = "octocat/hello-world",
                ),
                loading = true,
                error = null,
                setupAvailable = true,
                setupStarting = false,
                setupError = null,
                onCheckAgain = {},
                onSetup = {},
            )
        }
    }
}
