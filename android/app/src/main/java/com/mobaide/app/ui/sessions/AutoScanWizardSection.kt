package com.mobaide.app.ui.sessions

import android.content.res.Configuration
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material.icons.outlined.Bookmarks
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Lightbulb
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
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_GITHUB
import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_LINEAR
import com.mobaide.app.data.network.dto.AUTO_SCAN_UI_CHAT
import com.mobaide.app.data.network.dto.AUTO_SCAN_UI_TERMINAL
import com.mobaide.app.data.network.dto.AutoScanCapability
import com.mobaide.app.data.network.dto.AutoScanTemplateDto
import com.mobaide.app.data.network.dto.AutoScanTemplateSettingsDto
import com.mobaide.app.data.network.dto.ChatSupport
import com.mobaide.app.data.network.dto.GitHubIssueTypeDto
import com.mobaide.app.data.network.dto.GitHubLabelDto
import com.mobaide.app.data.network.dto.GitHubStatusDto
import com.mobaide.app.data.network.dto.WorkspaceInfo
import com.mobaide.app.ui.autoscan.autoScanAgentName
import com.mobaide.app.ui.common.AutoScanLinearFilterEditor
import com.mobaide.app.ui.common.LinearFilterErrors
import com.mobaide.app.ui.common.SecretTextField
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelCard
import com.mobaide.app.ui.common.pixel.PixelRadio
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

private const val GITHUB_LABEL_SUGGESTIONS_SHOWN = 30

internal const val RUN_WITHOUT_ASKING_OFF_HINT =
    "When off, the agent may stop and wait for your approval. You get a notification and can " +
        "open the session to approve."

// Codex has no on/off switch: its presets other than Full access may ask.
private const val CODEX_ASK_HINT =
    "Without Full access, Codex may stop and wait for your approval. You get a notification and can " +
        "open the session to approve."

/**
 * The Auto Scan step for an agent with `capabilities.autoscan`: templates, how
 * a scan works, then four numbered sections — where to find issues, which
 * issues, the AI agent and its options, and the instructions for every issue.
 */
@Composable
internal fun AutoScanWizardSection(
    state: CreateSessionUiState,
    capability: AutoScanCapability,
    viewModel: CreateSessionViewModel,
) {
    val wizard = state.autoScan
    val workspace = state.selectedWorkspace

    Text(
        text = "An AI agent picks up matching issues one by one and works on each in its own session.",
        style = MaterialTheme.typography.bodyMedium,
        modifier = Modifier.testTag("autoscan_intro"),
    )

    AutoScanTemplatesCard(
        ui = state.autoScanTemplates,
        onApply = viewModel::applyAutoScanTemplate,
        onDelete = viewModel::deleteAutoScanTemplate,
        onRetry = { viewModel.loadAutoScanTemplates(force = true) },
        onOpenSave = viewModel::openAutoScanTemplateSave,
        onDismissSave = viewModel::dismissAutoScanTemplateSave,
        onSave = viewModel::saveAutoScanTemplate,
    )

    HowAutoScanWorksCallout(
        source = wizard.source,
        isGit = workspace?.is_git != false,
        doneState = wizard.linearFilter.doneState,
    )

    NumberedSectionHeader(1, "Where to find issues")
    AutoScanSourceCards(
        sources = capability.sources,
        selected = wizard.source,
        onSelect = viewModel::setAutoScanSource,
    )

    NumberedSectionHeader(2, "Which issues")
    if (wizard.source == null) {
        Hint("Choose where to find issues first.")
    } else {
        when (wizard.source) {
            AUTO_SCAN_SOURCE_GITHUB -> GitHubSourcePanel(
                wizard = wizard,
                setupAvailable = capability.githubSetup,
                onCheck = viewModel::refreshGitHubStatus,
                onSetup = viewModel::startGitHubSetup,
                onToggleLabel = viewModel::toggleAutoScanGitHubLabel,
                onLabelQueryChange = viewModel::setAutoScanGitHubLabelQuery,
                onRetryLabels = viewModel::retryGitHubLabels,
                onToggleIssueType = viewModel::toggleAutoScanGitHubIssueType,
                onAssigneeChange = viewModel::setAutoScanGitHubAssignee,
                onActiveLabelChange = viewModel::setAutoScanGitHubActiveLabel,
                tipsHidden = state.autoScanTipsHidden,
                onTipsHiddenChange = viewModel::setAutoScanTipsHidden,
            )
            AUTO_SCAN_SOURCE_LINEAR -> if (workspace != null) {
                LinearSourcePanel(workspace = workspace, wizard = wizard, viewModel = viewModel)
            }
        }
    }

    NumberedSectionHeader(3, "AI agent")
    val cli = state.autoScanCli
    AutoScanAgentCards(
        clis = capability.clis,
        selected = cli,
        isAvailable = { state.cliAvailability(it) == true },
        onSelect = viewModel::setAutoScanCli,
    )
    if (cli != null) {
        // Before the options: choosing Chat hides some of them (Claude's
        // Focus view), which must not move this control under the finger.
        AutoScanSessionViewField(
            cli = cli,
            chatAvailable = state.autoScanChatAvailable,
            chatSupport = state.chatSupport(cli),
            selected = state.autoScanUi,
            onSelect = viewModel::setAutoScanUi,
        )
        AutoScanAgentOptions(cli = cli, state = state, viewModel = viewModel)
    }

    NumberedSectionHeader(4, "Instructions and schedule")
    AutoScanInitialPromptField(
        value = state.initialPrompt,
        onChange = viewModel::setInitialPrompt,
        label = "Instructions for the agent (optional)",
    )
    Hint("Your playbook for every issue. The issue's title, link and description are added after it.")

    AutoScanIntervalField(
        minutes = wizard.intervalMinutes,
        onSelect = viewModel::setAutoScanInterval,
    )

    HorizontalDivider(thickness = 2.dp, color = MaterialTheme.pixel.rule)

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

/** The chosen agent's Auto Scan options: the same controls as its own New session step. */
@Composable
private fun AutoScanAgentOptions(
    cli: String,
    state: CreateSessionUiState,
    viewModel: CreateSessionViewModel,
) {
    val agents = state.autoScan.agents
    when (cli) {
        "claude" -> AutoScanClaudeOptions(state = state, viewModel = viewModel)
        "codex" -> {
            val codex = agents.codex
            CodexOptionControls(
                preset = codex.preset,
                models = state.codexModels?.takeIf { it.isNotEmpty() } ?: CreateSessionViewModel.CODEX_MODELS,
                model = codex.model,
                effort = codex.reasoningEffort,
                efforts = state.codexEffortsFor(codex.model),
                sandbox = codex.sandbox,
                approval = codex.approval,
                fullAuto = codex.fullAuto,
                onPreset = viewModel::selectAutoScanCodexPreset,
                onModel = viewModel::setAutoScanCodexModel,
                onEffort = viewModel::setAutoScanCodexReasoningEffort,
                onAgentDefaultEffort = { viewModel.setAutoScanCodexReasoningEffort(null) },
                onSandbox = viewModel::setAutoScanCodexSandbox,
                onApproval = viewModel::setAutoScanCodexApproval,
                autonomyHint = CODEX_ASK_HINT.takeIf { !codex.fullAuto && codex.approval != "never" },
            )
        }
        "antigravity" -> {
            val antigravity = agents.antigravity
            AntigravityOptionControls(
                models = state.antigravityModels?.takeIf { it.isNotEmpty() }
                    ?: CreateSessionViewModel.ANTIGRAVITY_MODELS,
                model = antigravity.model,
                skipPermissions = antigravity.skipPermissions,
                sandbox = antigravity.sandbox,
                addDirsDraft = antigravity.addDirsDraft,
                addDirsError = antigravity.addDirsError,
                onModel = viewModel::setAutoScanAntigravityModel,
                onSkipPermissions = viewModel::setAutoScanAntigravitySkipPermissions,
                onSandbox = viewModel::setAutoScanAntigravitySandbox,
                onAddDirsChange = viewModel::setAutoScanAntigravityAddDirsDraft,
                skipPermissionsHint = RUN_WITHOUT_ASKING_OFF_HINT.takeIf { !antigravity.skipPermissions },
            )
        }
        "opencode" -> {
            OpenCodePermissionControls(agents.opencodeAutoApprove, viewModel::setAutoScanOpenCodeAutoApprove)
            if (!agents.opencodeAutoApprove) AskHint(RUN_WITHOUT_ASKING_OFF_HINT)
        }
        "copilot" -> {
            CopilotPermissionControls(agents.copilotAllowAll, viewModel::setAutoScanCopilotAllowAll)
            if (!agents.copilotAllowAll) AskHint(RUN_WITHOUT_ASKING_OFF_HINT)
        }
        "cursor" -> {
            CursorPermissionControls(agents.cursorForce, viewModel::setAutoScanCursorForce)
            if (!agents.cursorForce) AskHint(RUN_WITHOUT_ASKING_OFF_HINT)
        }
        "junie" -> {
            JunieOptionControls(
                planMode = agents.juniePlanMode,
                brave = agents.junieBrave,
                onPlanModeChange = viewModel::setAutoScanJuniePlanMode,
                onBraveChange = viewModel::setAutoScanJunieBrave,
            )
            if (!agents.junieBrave) AskHint(RUN_WITHOUT_ASKING_OFF_HINT)
        }
        "pi" -> Hint("Pi runs its tools without asking.")
    }
}

/** A numbered step heading: a square brand badge with the number, then the title. */
@Composable
private fun NumberedSectionHeader(number: Int, title: String) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 12.dp)
            .semantics(mergeDescendants = true) { heading() }
            .testTag("autoscan_section_$number"),
    ) {
        Box(
            modifier = Modifier
                .size(28.dp)
                .background(MaterialTheme.pixel.brand)
                .pixelFrame(MaterialTheme.pixel.brand2),
            contentAlignment = Alignment.Center,
        ) {
            Text(text = "$number", style = MaterialTheme.typography.labelLarge, color = Color.White)
        }
        Text(text = title, style = MaterialTheme.typography.titleMedium)
    }
}

/**
 * A tinted plate with an icon, a title and short lines: [tone] colours the
 * ring, the icon and a light wash of the fill.
 */
@Composable
private fun AutoScanCallout(
    title: String,
    icon: ImageVector,
    tone: Color,
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        color = tone.copy(alpha = 0.10f),
        border = BorderStroke(2.dp, tone),
        shape = RectangleShape,
        modifier = modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(imageVector = icon, contentDescription = null, tint = tone, modifier = Modifier.size(22.dp))
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.semantics { heading() },
                )
                content()
            }
        }
    }
}

@Composable
private fun CalloutLine(text: String) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(text = "•", style = MaterialTheme.typography.bodyMedium)
        Text(text = text, style = MaterialTheme.typography.bodyMedium)
    }
}

/**
 * Says once what every scan does with an issue: its own worktree and branch, a
 * session that starts at once, and when the worktree goes away.
 */
@Composable
private fun HowAutoScanWorksCallout(source: String?, isGit: Boolean, doneState: String) {
    AutoScanCallout(
        title = "How Auto Scan works",
        icon = Icons.Outlined.Info,
        tone = MaterialTheme.pixel.accent,
        modifier = Modifier.testTag("autoscan_how_it_works"),
    ) {
        CalloutLine(
            if (isGit) {
                "Each issue runs in its own git worktree and branch, so the agent's work never touches " +
                    "your working copy."
            } else {
                "This folder is not a git repository, so each issue runs in the folder itself."
            },
        )
        CalloutLine("Each session starts at once with the issue as its prompt. You get a notification when it needs you.")
        if (isGit) {
            CalloutLine("Auto Scan trusts each worktree it creates, so the agent does not ask to trust the folder.")
        }
        if (isGit) {
            when (source) {
                AUTO_SCAN_SOURCE_GITHUB -> CalloutLine(
                    "When an issue is closed, its worktree is removed. A worktree with uncommitted changes is kept.",
                )
                AUTO_SCAN_SOURCE_LINEAR -> CalloutLine(
                    "When an issue moves to “${doneState.trim().ifEmpty { "Done" }}”, its worktree is removed.",
                )
            }
        }
    }
}

/**
 * Saved Auto Scan templates: a picker that applies one at once, a delete per
 * entry behind a confirm, and "Save as template…". Errors stay in the card
 * and never block Start.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AutoScanTemplatesCard(
    ui: AutoScanTemplatesUi,
    onApply: (String) -> Unit,
    onDelete: (String) -> Unit,
    onRetry: () -> Unit,
    onOpenSave: () -> Unit,
    onDismissSave: () -> Unit,
    onSave: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    var deleteTarget by remember { mutableStateOf<AutoScanTemplateDto?>(null) }
    val empty = ui.templates.isEmpty()
    // A dialog hands focus back to the last focused field when it closes; that
    // field can be far down the step and would scroll the notice away.
    val focusManager = LocalFocusManager.current

    PixelCard(modifier = Modifier.fillMaxWidth().testTag("autoscan_templates")) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    imageVector = Icons.Outlined.Bookmarks,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(20.dp),
                )
                Text("Templates", style = MaterialTheme.typography.titleSmall)
            }
            ExposedDropdownMenuBox(
                expanded = expanded && !empty,
                onExpandedChange = { if (!empty) expanded = !expanded },
            ) {
                OutlinedTextField(
                    colors = PixelTextFieldDefaults.colors(),
                    value = ui.appliedName ?: "Choose a template",
                    onValueChange = {},
                    readOnly = true,
                    enabled = !empty,
                    label = { Text("Template") },
                    trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
                    modifier = Modifier
                        .menuAnchor()
                        .fillMaxWidth()
                        .testTag("autoscan_template_picker"),
                )
                DropdownMenu(
                    expanded = expanded && !empty,
                    onDismissRequest = { expanded = false },
                ) {
                    ui.templates.forEach { template ->
                        DropdownMenuItem(
                            text = {
                                Column {
                                    Text(template.name)
                                    Text(
                                        text = template.summary,
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            },
                            onClick = {
                                expanded = false
                                onApply(template.id)
                            },
                            trailingIcon = {
                                IconButton(
                                    onClick = {
                                        expanded = false
                                        focusManager.clearFocus()
                                        deleteTarget = template
                                    },
                                    modifier = Modifier.testTag("autoscan_template_delete_${template.id}"),
                                ) {
                                    Icon(Icons.Outlined.Delete, contentDescription = "Delete template ${template.name}")
                                }
                            },
                            modifier = Modifier.testTag("autoscan_template_${template.id}"),
                        )
                    }
                }
            }
            if (ui.loading) LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            when {
                ui.loadError != null -> Row(verticalAlignment = Alignment.CenterVertically) {
                    ErrorText(ui.loadError, Modifier.weight(1f))
                    PixelTextButton(onClick = onRetry) { Text("Retry") }
                }
                empty && !ui.loading -> Hint("No templates yet. Set up a scan, then save it as a template.")
            }
            ui.message?.let {
                Text(
                    text = it,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.pixel.ok,
                    modifier = Modifier.testTag("autoscan_template_message"),
                )
            }
            ui.deleteError?.let { ErrorText(it) }
            PixelButton(
                style = PixelButtonStyle.Ghost,
                onClick = {
                    focusManager.clearFocus()
                    onOpenSave()
                },
                // Until the list loads, a clashing name could not be shown before it replaced a template.
                enabled = ui.canSave,
                modifier = Modifier.testTag("autoscan_template_save"),
            ) { Text("Save as template…") }
        }
    }

    if (ui.saveDialogOpen) {
        SaveTemplateDialog(
            templates = ui.templates,
            initialName = ui.appliedName.orEmpty(),
            saving = ui.saving,
            error = ui.saveError,
            onDismiss = onDismissSave,
            onSave = onSave,
        )
    }
    deleteTarget?.let { template ->
        PixelAlertDialog(
            onDismissRequest = { deleteTarget = null },
            title = { Text("Delete template “${template.name}”?") },
            confirmButton = {
                PixelButton(
                    style = PixelButtonStyle.Danger,
                    onClick = {
                        deleteTarget = null
                        onDelete(template.id)
                    },
                    modifier = Modifier.testTag("autoscan_template_delete_confirm"),
                ) { Text("Delete") }
            },
            dismissButton = {
                PixelTextButton(onClick = { deleteTarget = null }) { Text("Cancel") }
            },
        )
    }
}

/** The name of a new template; a name in use (ignoring case and spaces) replaces that one. */
@Composable
private fun SaveTemplateDialog(
    templates: List<AutoScanTemplateDto>,
    initialName: String,
    saving: Boolean,
    error: String?,
    onDismiss: () -> Unit,
    onSave: (String) -> Unit,
) {
    var name by rememberSaveable { mutableStateOf(initialName) }
    val replaces = templates.firstOrNull { templateNameKey(it.name) == templateNameKey(name) && name.isNotBlank() }
    val valid = name.isNotBlank() && name.trim().length <= TEMPLATE_NAME_MAX
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Save as template") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    colors = PixelTextFieldDefaults.colors(),
                    value = name,
                    onValueChange = { name = it.take(TEMPLATE_NAME_MAX) },
                    label = { Text("Name") },
                    singleLine = true,
                    enabled = !saving,
                    supportingText = { Text("${name.trim().length} / $TEMPLATE_NAME_MAX") },
                    keyboardOptions = KeyboardOptions(
                        capitalization = KeyboardCapitalization.Sentences,
                        imeAction = ImeAction.Done,
                    ),
                    keyboardActions = KeyboardActions(onDone = { if (valid && !saving) onSave(name) }),
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag("autoscan_template_name"),
                )
                replaces?.let { Hint("Replaces the template “${it.name}”.") }
                error?.let { ErrorText(it, Modifier.testTag("autoscan_template_save_error")) }
            }
        },
        confirmButton = {
            PixelButton(
                style = PixelButtonStyle.Primary,
                onClick = { onSave(name) },
                enabled = valid && !saving,
                modifier = Modifier.testTag("autoscan_template_save_confirm"),
            ) { Text(if (saving) "Saving…" else if (replaces != null) "Replace" else "Save") }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss, enabled = !saving) { Text("Cancel") }
        },
    )
}

/**
 * A full-width selectable plate with a radio square, a title and a hint; the
 * pattern for every one-of-many choice on this step.
 */
@Composable
private fun ChoiceCard(
    title: String,
    hint: String?,
    selected: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    PixelCard(
        selected = selected,
        onClick = onClick,
        enabled = enabled,
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .alpha(if (enabled) 1f else 0.55f)
            .semantics {
                this.selected = selected
                role = Role.RadioButton
            },
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 4.dp, vertical = 6.dp),
        ) {
            PixelRadio(selected = selected, onClick = null, enabled = enabled)
            Column(modifier = Modifier.padding(end = 8.dp)) {
                Text(text = title, style = MaterialTheme.typography.titleSmall)
                if (hint != null) {
                    Text(
                        text = hint,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun AutoScanSourceCards(
    sources: List<String>,
    selected: String?,
    onSelect: (String) -> Unit,
) {
    val options = listOf(
        Triple(
            AUTO_SCAN_SOURCE_GITHUB,
            "GitHub Issues",
            "Open issues of this workspace's GitHub repository, read with the GitHub CLI (gh) on the PC.",
        ),
        Triple(
            AUTO_SCAN_SOURCE_LINEAR,
            "Linear",
            "Issues of your Linear team, read with this workspace's Linear API key.",
        ),
    ).filter { (source, _, _) -> source in sources }
    Column(
        verticalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.selectableGroup(),
    ) {
        options.forEach { (source, title, hint) ->
            ChoiceCard(
                title = title,
                hint = hint,
                selected = selected == source,
                enabled = true,
                onClick = { onSelect(source) },
                modifier = Modifier.testTag("autoscan_source_$source"),
            )
        }
    }
}

/** The agents Auto Scan can run, two per row; one not installed on the PC shows but cannot be chosen. */
@Composable
private fun AutoScanAgentCards(
    clis: List<String>,
    selected: String?,
    isAvailable: (String) -> Boolean,
    onSelect: (String) -> Unit,
) {
    Column(
        verticalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier
            .selectableGroup()
            .testTag("autoscan_agent"),
    ) {
        clis.chunked(2).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { cli ->
                    val available = isAvailable(cli)
                    ChoiceCard(
                        title = autoScanAgentName(cli),
                        hint = if (available) null else "Not installed on the PC",
                        selected = selected == cli,
                        enabled = available,
                        onClick = { onSelect(cli) },
                        modifier = Modifier
                            .weight(1f)
                            .testTag("autoscan_agent_$cli"),
                    )
                }
                if (row.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
    if (selected == null) Hint("Choose the agent that works on each issue, in a session of its own.")
}

@Composable
private fun GitHubSourcePanel(
    wizard: AutoScanWizardState,
    setupAvailable: Boolean,
    onCheck: (Boolean) -> Unit,
    onSetup: (String) -> Unit,
    onToggleLabel: (String) -> Unit,
    onLabelQueryChange: (String) -> Unit,
    onRetryLabels: () -> Unit,
    onToggleIssueType: (String) -> Unit,
    onAssigneeChange: (String) -> Unit,
    onActiveLabelChange: (String) -> Unit,
    tipsHidden: Boolean,
    onTipsHiddenChange: (Boolean) -> Unit,
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

    PickedUpByLabelsCallout()
    LabelTip(hidden = tipsHidden, onHiddenChange = onTipsHiddenChange)

    GitHubLabelsField(
        selected = wizard.githubFilter.labels,
        repoLabels = wizard.githubLabels,
        loading = wizard.githubLabelsLoading,
        loadError = wizard.githubLabelsError,
        query = wizard.githubLabelQuery,
        limitError = wizard.githubLabelLimitError,
        onToggle = onToggleLabel,
        onQueryChange = onLabelQueryChange,
        onRetry = onRetryLabels,
    )

    GitHubIssueTypesField(
        chosen = wizard.githubFilter.issueTypes,
        repoTypes = wizard.githubIssueTypes,
        loaded = wizard.githubLabelsFor != null && !wizard.githubLabelsLoading && wizard.githubLabelsError == null,
        onToggle = onToggleIssueType,
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
}

/** The rule that decides which issues run, in the attention colour: it is easy to get wrong. */
@Composable
private fun PickedUpByLabelsCallout() {
    AutoScanCallout(
        title = "How issues are picked up",
        icon = Icons.AutoMirrored.Outlined.Label,
        tone = MaterialTheme.pixel.warning,
        modifier = Modifier.testTag("autoscan_github_pickup_rule"),
    ) {
        Text(
            text = "An open issue with any of these labels is picked up. When work starts, the scan removes " +
                "the label, so each issue runs only once. Add the label again to run the issue again.",
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

private val LABEL_TIPS = listOf(
    "Create a dedicated label, for example auto-scan, and add it only to the issues an agent should solve.",
    "Avoid common labels such as bug: every open issue with them would start a session.",
    "Keep the label out of issue templates and workflows that label new issues, or anyone who opens an " +
        "issue could start a session on your PC.",
    "Use one label per kind of work, such as auto-scan-small and auto-scan-complex, and save a template for each.",
)

@Composable
private fun LabelTip(hidden: Boolean, onHiddenChange: (Boolean) -> Unit) {
    if (hidden) {
        PixelTextButton(
            onClick = { onHiddenChange(false) },
            modifier = Modifier.testTag("autoscan_github_tips_show"),
        ) { Text("Show tips") }
        return
    }
    Row(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.testTag("autoscan_github_label_tip"),
    ) {
        Icon(
            imageVector = Icons.Outlined.Lightbulb,
            contentDescription = null,
            tint = MaterialTheme.pixel.textMuted,
            modifier = Modifier.size(18.dp),
        )
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
                text = "Tips",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.semantics { heading() },
            )
            LABEL_TIPS.forEach { tip ->
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Hint("•")
                    Hint(tip)
                }
            }
            PixelTextButton(
                onClick = { onHiddenChange(true) },
                modifier = Modifier.testTag("autoscan_github_tips_hide"),
            ) { Text("Hide tips") }
        }
    }
}

/**
 * Chat or Terminal for each issue's session. Chat is offered only when the
 * chosen agent has the chat view on this PC.
 */
@Composable
private fun AutoScanSessionViewField(
    cli: String,
    chatAvailable: Boolean,
    chatSupport: ChatSupport?,
    selected: String,
    onSelect: (String) -> Unit,
) {
    val junieTrust = chatAvailable && cli == "junie" && selected == AUTO_SCAN_UI_TERMINAL
    SessionViewField(
        chatSelected = selected == AUTO_SCAN_UI_CHAT,
        chatEnabled = chatAvailable,
        onSelect = { chat -> onSelect(if (chat) AUTO_SCAN_UI_CHAT else AUTO_SCAN_UI_TERMINAL) },
        hint = when {
            // The probe has not answered yet: the shared hint knows only its answers.
            !chatAvailable -> chatUnavailableHint(chatSupport, cli) ?: "Checking the chat view on the PC…"
            junieTrust -> "Junie asks to trust a new worktree unless you trusted the workspace folder in Junie. " +
                "Trust it once in Junie, or use Chat."
            else -> null
        },
        hintModifier = if (junieTrust) Modifier.testTag("autoscan_junie_trust_hint") else Modifier,
        testTag = "autoscan_session_view",
    )
}

/** The wait between two scans; a saved value outside the list still shows. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AutoScanIntervalField(minutes: Int, onSelect: (Int) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    val options = if (minutes in AUTO_SCAN_INTERVALS) AUTO_SCAN_INTERVALS else (AUTO_SCAN_INTERVALS + minutes).sorted()
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = !expanded },
        modifier = Modifier.testTag("autoscan_interval"),
    ) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = autoScanIntervalLabel(minutes),
            onValueChange = {},
            readOnly = true,
            label = { Text("Time between scans") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier
                .menuAnchor()
                .fillMaxWidth(),
        )
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { value ->
                DropdownMenuItem(
                    text = { Text(autoScanIntervalLabel(value)) },
                    onClick = {
                        onSelect(value)
                        expanded = false
                    },
                )
            }
        }
    }
    Hint("After each scan, Auto Scan waits this long before it looks for new issues. Run now starts a scan at once.")
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
    val ready = status != null && status.problem == null
    PixelCard(
        modifier = Modifier
            .fillMaxWidth()
            .testTag("autoscan_github_status"),
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (loading) LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                if (status != null) {
                    Icon(
                        imageVector = if (ready) Icons.Filled.CheckCircle else Icons.Filled.Warning,
                        contentDescription = if (ready) "Ready" else "Needs attention",
                        tint = if (ready) MaterialTheme.pixel.ok else MaterialTheme.pixel.warning,
                        modifier = Modifier.size(20.dp),
                    )
                }
                Column(modifier = Modifier.weight(1f)) {
                    when {
                        status == null && error != null -> ErrorText(error)
                        status == null -> Text("Checking GitHub on the PC…")
                        !status.installed -> Text(status.problem ?: "GitHub CLI (gh) is not installed on the PC.")
                        status.offersSignIn ->
                            Text(status.problem ?: "GitHub CLI is not signed in on the PC.")
                        status.problem != null -> Text(status.problem)
                        else -> {
                            Text(status.repo?.let { "Repository $it" } ?: "GitHub is ready.")
                            status.account?.let { Hint("Signed in as $it") }
                        }
                    }
                }
            }
            when {
                status == null -> Unit
                !status.installed -> if (status.can_install && setupAvailable) {
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
                status.offersSignIn -> if (setupAvailable) {
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

/**
 * The trigger labels: the chosen ones as removable chips (also names the
 * repository does not list, e.g. from a template), then the repository's other
 * labels to tap. A search field filters them when there are more than fit; it
 * never adds a name.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun GitHubLabelsField(
    selected: List<String>,
    repoLabels: List<GitHubLabelDto>,
    loading: Boolean,
    loadError: String?,
    query: String,
    limitError: String?,
    onToggle: (String) -> Unit,
    onQueryChange: (String) -> Unit,
    onRetry: () -> Unit,
) {
    Text("Labels to pick up", style = MaterialTheme.typography.labelLarge)
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
    if (loading) LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
    if (loadError != null) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            ErrorText(loadError, Modifier.weight(1f))
            PixelTextButton(onClick = onRetry, modifier = Modifier.testTag("autoscan_github_labels_retry")) {
                Text("Try again")
            }
        }
        return
    }
    val unchosen = repoLabels.map { it.name }.filter { it !in selected }
    if (unchosen.size > GITHUB_LABEL_SUGGESTIONS_SHOWN || query.isNotEmpty()) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = query,
            onValueChange = onQueryChange,
            label = { Text("Search labels") },
            singleLine = true,
            leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
            trailingIcon = if (query.isNotEmpty()) {
                {
                    IconButton(onClick = { onQueryChange("") }) {
                        Icon(Icons.Filled.Close, contentDescription = "Clear search")
                    }
                }
            } else {
                null
            },
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.None,
                autoCorrectEnabled = false,
                imeAction = ImeAction.Search,
            ),
            modifier = Modifier
                .fillMaxWidth()
                .testTag("autoscan_github_label_search"),
        )
    }
    val needle = query.trim()
    val suggestions = unchosen.filter { needle.isEmpty() || it.contains(needle, ignoreCase = true) }
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
        if (hidden > 0) Hint("$hidden more ${if (hidden == 1) "label" else "labels"}: search to find them.")
    } else if (needle.isNotEmpty()) {
        Hint("No label matches “$needle”.")
    } else if (!loading && repoLabels.isEmpty() && selected.isEmpty()) {
        Hint("This repository has no labels yet. Create one on GitHub, then tap Check again.")
    }
    limitError?.let { ErrorText(it) }
}

/**
 * The GitHub issue types to take: the repository's types, plus chosen names
 * it does not list (from a template), which stay removable.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun GitHubIssueTypesField(
    chosen: List<String>,
    repoTypes: List<GitHubIssueTypeDto>,
    loaded: Boolean,
    onToggle: (String) -> Unit,
) {
    Text("Issue types", style = MaterialTheme.typography.labelLarge)
    Hint("Only issues of the chosen types are picked up. None chosen: any type.")
    val names = repoTypes.map { it.name } +
        chosen.filter { type -> repoTypes.none { it.name.equals(type, ignoreCase = true) } }
    if (loaded && repoTypes.isEmpty()) Hint("This repository has no issue types.")
    if (names.isNotEmpty()) {
        FlowRow(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("autoscan_github_issue_types"),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            names.forEach { name ->
                FilterChip(
                    selected = chosen.any { it.equals(name, ignoreCase = true) },
                    onClick = { onToggle(name) },
                    label = { Text(name) },
                    modifier = Modifier.testTag("autoscan_github_issue_type_$name"),
                )
            }
        }
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
        description = "Issues assigned to you in one of these states, with one of these labels, are picked up. " +
            "When work starts, the scan moves the issue to the active state and removes the removal label.",
    )
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

@Preview(name = "Auto Scan parts light", showBackground = true)
@Preview(
    name = "Auto Scan parts dark",
    showBackground = true,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun AutoScanPartsPreview() {
    MobaideTheme {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            AutoScanTemplatesCard(
                ui = AutoScanTemplatesUi(
                    templates = listOf(
                        AutoScanTemplateDto(
                            id = "a",
                            name = "Small bugs",
                            settings = AutoScanTemplateSettingsDto(source = "github", cli = "codex"),
                        ),
                    ),
                    loaded = true,
                    appliedName = "Small bugs",
                    message = "Applied “Small bugs”.",
                ),
                onApply = {},
                onDelete = {},
                onRetry = {},
                onOpenSave = {},
                onDismissSave = {},
                onSave = {},
            )
            HowAutoScanWorksCallout(source = AUTO_SCAN_SOURCE_GITHUB, isGit = true, doneState = "")
            NumberedSectionHeader(1, "Where to find issues")
            AutoScanSourceCards(
                sources = listOf(AUTO_SCAN_SOURCE_GITHUB, AUTO_SCAN_SOURCE_LINEAR),
                selected = AUTO_SCAN_SOURCE_GITHUB,
                onSelect = {},
            )
            NumberedSectionHeader(2, "Which issues")
            GitHubStatusBlock(
                status = GitHubStatusDto(
                    installed = true,
                    signed_in = true,
                    account = "octocat",
                    repo = "octocat/hello-world",
                ),
                loading = false,
                error = null,
                setupAvailable = true,
                setupStarting = false,
                setupError = null,
                onCheckAgain = {},
                onSetup = {},
            )
            PickedUpByLabelsCallout()
            LabelTip(hidden = false, onHiddenChange = {})
            GitHubIssueTypesField(
                chosen = listOf("Bug"),
                repoTypes = listOf(GitHubIssueTypeDto("Bug", "RED"), GitHubIssueTypeDto("Task", "YELLOW")),
                loaded = true,
                onToggle = {},
            )
            NumberedSectionHeader(3, "AI agent")
            AutoScanAgentCards(
                clis = listOf("claude", "codex", "junie"),
                selected = "codex",
                isAvailable = { it != "junie" },
                onSelect = {},
            )
        }
    }
}
