package com.mobaide.app.ui.projects

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import androidx.compose.material3.OutlinedTextField
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProjectFormSheet(
    state: ProjectFormState,
    onDismiss: () -> Unit,
    onNameChange: (String) -> Unit,
    onPathChange: (String) -> Unit,
    onCondaChange: (String) -> Unit,
    onLinearKeyChange: (String) -> Unit,
    onToggleKeyVisible: () -> Unit,
    onRequestClearLinearKey: () -> Unit,
    onTouch: (ProjectFormState.Field) -> Unit,
    onAddFlag: () -> Unit,
    onUpdateFlag: (Int, FlagEntry) -> Unit,
    onRemoveFlag: (Int) -> Unit,
    onToggleTriggerLabel: (String) -> Unit,
    onToggleTriggerState: (String) -> Unit,
    onActiveStateChange: (String) -> Unit,
    onDoneStateChange: (String) -> Unit,
    onRemovalLabelChange: (String) -> Unit,
    onRefreshFilterMetadata: () -> Unit,
    onSave: () -> Unit,
    onOpenGit: (String) -> Unit = {},
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    PixelBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .imePadding()
                .navigationBarsPadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = if (state.isEdit) "Edit project" else "New project",
                style = MaterialTheme.typography.titleLarge,
            )

            OutlinedTextField(
                colors = PixelTextFieldDefaults.colors(),
                value = state.name,
                onValueChange = {
                    onNameChange(it)
                    onTouch(ProjectFormState.Field.NAME)
                },
                label = { Text("Name") },
                isError = state.nameError != null,
                supportingText = { state.nameError?.let { Text(it) } },
                singleLine = true,
                // Unlike a template name, this one is a constrained identifier —
                // `VALID_NAME` in ProjectFormState allows only letters, digits,
                // spaces and `_-.`, so an autocorrected apostrophe or smart quote
                // fails validation outright.
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.None,
                    autoCorrectEnabled = false,
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .blurTouchTrigger { onTouch(ProjectFormState.Field.NAME) },
            )

            OutlinedTextField(
                colors = PixelTextFieldDefaults.colors(),
                value = state.path,
                onValueChange = {
                    onPathChange(it)
                    onTouch(ProjectFormState.Field.PATH)
                },
                label = { Text("Path") },
                isError = state.pathError != null,
                supportingText = { state.pathError?.let { Text(it) } },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.None,
                    autoCorrectEnabled = false,
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .blurTouchTrigger { onTouch(ProjectFormState.Field.PATH) },
            )

            OutlinedTextField(
                colors = PixelTextFieldDefaults.colors(),
                value = state.condaEnv,
                onValueChange = {
                    onCondaChange(it)
                    onTouch(ProjectFormState.Field.CONDA)
                },
                label = { Text("Conda env (optional)") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.None,
                    autoCorrectEnabled = false,
                ),
                modifier = Modifier.fillMaxWidth(),
            )

            LinearKeyField(
                value = state.linearApiKey,
                revealed = state.linearKeyVisible,
                isEdit = state.isEdit,
                clearRequested = state.clearLinearKey,
                onChange = {
                    onLinearKeyChange(it)
                    onTouch(ProjectFormState.Field.KEY)
                },
                onToggleVisible = onToggleKeyVisible,
                onClearRequested = onRequestClearLinearKey,
            )

            FlagsEditor(
                flags = state.flags,
                keyErrors = state.flagKeyErrors,
                valueErrors = state.flagValueErrors,
                onUpdate = onUpdateFlag,
                onRemove = onRemoveFlag,
                onAdd = onAddFlag,
            )

            if (state.showAutoScanFilterSection) {
                HorizontalDivider(thickness = 2.dp)
                AutoScanFilterSection(
                    state = state,
                    onToggleTriggerLabel = onToggleTriggerLabel,
                    onToggleTriggerState = onToggleTriggerState,
                    onActiveStateChange = onActiveStateChange,
                    onDoneStateChange = onDoneStateChange,
                    onRemovalLabelChange = onRemovalLabelChange,
                    onRefresh = onRefreshFilterMetadata,
                )
            }

            Spacer(Modifier.height(8.dp))

            state.submitError?.let { msg ->
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    color = MaterialTheme.colorScheme.errorContainer,
                    contentColor = MaterialTheme.colorScheme.onErrorContainer,
                    shape = MaterialTheme.shapes.medium,
                ) {
                    Text(
                        text = msg,
                        style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                    )
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End,
            ) {
                val gitProject = state.editing
                if (gitProject?.is_git == true) {
                    PixelTextButton(onClick = { onOpenGit(gitProject.name) }) { Text("Git") }
                    Spacer(Modifier.width(8.dp))
                }
                PixelTextButton(onClick = onDismiss) { Text("Cancel") }
                Spacer(Modifier.width(8.dp))
                PixelButton(
                    style = PixelButtonStyle.Primary,
                    onClick = onSave,
                    enabled = !state.submitting,
                ) {
                    Text(if (state.submitting) "Saving…" else "Save")
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LinearKeyField(
    value: String,
    revealed: Boolean,
    isEdit: Boolean,
    clearRequested: Boolean,
    onChange: (String) -> Unit,
    onToggleVisible: () -> Unit,
    onClearRequested: () -> Unit,
) {
    val placeholder = when {
        isEdit && clearRequested -> "Will be cleared on save"
        isEdit -> "Leave blank to keep current"
        else -> ""
    }
    Column(modifier = Modifier.fillMaxWidth()) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = value,
            onValueChange = onChange,
            label = { Text("Linear API key (optional)") },
            placeholder = { Text(placeholder) },
            visualTransformation = if (revealed) VisualTransformation.None else PasswordVisualTransformation(),
            singleLine = true,
            trailingIcon = {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onToggleVisible) {
                        Icon(
                            imageVector = if (revealed) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                            contentDescription = if (revealed) "Hide key" else "Show key",
                        )
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
        )
        if (isEdit) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End,
            ) {
                PixelTextButton(onClick = onClearRequested, enabled = !clearRequested) {
                    Text(if (clearRequested) "Will clear" else "Clear key")
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun AutoScanFilterSection(
    state: ProjectFormState,
    onToggleTriggerLabel: (String) -> Unit,
    onToggleTriggerState: (String) -> Unit,
    onActiveStateChange: (String) -> Unit,
    onDoneStateChange: (String) -> Unit,
    onRemovalLabelChange: (String) -> Unit,
    onRefresh: () -> Unit,
) {
    // Union of "what we know exists in Linear" with "what the form already
    // has selected". The latter guarantees that even before the discovery
    // call lands (or if it failed), the user sees their currently-saved
    // selections rendered as chips, so the form is functional offline.
    val labelNames = remember(state.availableLabels, state.triggerLabels, state.removalLabel) {
        val seen = LinkedHashSet<String>()
        state.availableLabels.forEach { seen.add(it.name) }
        seen.addAll(state.triggerLabels)
        if (state.removalLabel.isNotBlank()) seen.add(state.removalLabel)
        seen.toList()
    }
    val stateNames = remember(state.availableStates, state.triggerStates, state.activeState, state.doneState) {
        val seen = LinkedHashSet<String>()
        state.availableStates.forEach { seen.add(it.name) }
        seen.addAll(state.triggerStates)
        if (state.activeState.isNotBlank()) seen.add(state.activeState)
        if (state.doneState.isNotBlank()) seen.add(state.doneState)
        seen.toList()
    }
    val stateByName = remember(state.availableStates) {
        state.availableStates.associateBy { it.name }
    }

    Column(
        modifier = Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "Auto Scan filters",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f),
            )
            IconButton(
                onClick = onRefresh,
                enabled = !state.filterMetadataLoading,
            ) {
                Icon(
                    Icons.Default.Refresh,
                    contentDescription = "Refresh from Linear",
                )
            }
        }

        Text(
            text = "Each matching issue runs in its own git worktree " +
                "(branched per Linear issue), so its changes stay isolated " +
                "from your working copy. Projects that aren't git repositories " +
                "run in the project directory directly.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        if (state.filterMetadataLoading) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
        }

        state.filterMetadataError?.let { err ->
            Surface(
                modifier = Modifier.fillMaxWidth(),
                color = MaterialTheme.colorScheme.errorContainer,
                contentColor = MaterialTheme.colorScheme.onErrorContainer,
                shape = MaterialTheme.shapes.small,
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = err,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.weight(1f),
                    )
                    PixelTextButton(onClick = onRefresh) { Text("Retry") }
                }
            }
        }

        ChipGroupSection(
            title = "Trigger labels",
            description = "Issues with any of these labels will be picked up.",
            error = state.triggerLabelsError,
            names = labelNames,
            selected = state.triggerLabels,
            empty = if (labelNames.isEmpty())
                "No labels found — verify your Linear API key and refresh."
            else null,
            onToggle = onToggleTriggerLabel,
            chipLeading = null,
        )

        ChipGroupSection(
            title = "Trigger states",
            description = "Issues in any of these workflow states will be picked up.",
            error = state.triggerStatesError,
            names = stateNames,
            selected = state.triggerStates,
            empty = if (stateNames.isEmpty())
                "No workflow states found — verify your Linear API key and refresh."
            else null,
            onToggle = onToggleTriggerState,
            chipLeading = { name ->
                val type = stateByName[name]?.type.orEmpty()
                if (type.isNotBlank()) {
                    Text(
                        text = stateTypeGlyph(type),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            },
        )

        SingleSelectDropdown(
            label = "Active state (set on claim)",
            value = state.activeState,
            options = stateNames,
            error = state.activeStateError,
            onChange = onActiveStateChange,
        )

        SingleSelectDropdown(
            label = "Done state (cleanup trigger)",
            value = state.doneState,
            options = stateNames,
            error = state.doneStateError,
            onChange = onDoneStateChange,
        )

        SingleSelectDropdown(
            label = "Removal label (stripped on claim)",
            value = state.removalLabel,
            options = labelNames,
            error = state.removalLabelError,
            onChange = onRemovalLabelChange,
        )
    }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun ChipGroupSection(
    title: String,
    description: String,
    error: String?,
    names: List<String>,
    selected: Set<String>,
    empty: String?,
    onToggle: (String) -> Unit,
    chipLeading: (@Composable (String) -> Unit)?,
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(text = title, style = MaterialTheme.typography.labelLarge)
        Text(
            text = description,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (empty != null) {
            Text(
                text = empty,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        } else {
            FlowRow(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                names.forEach { name ->
                    FilterChip(
                        selected = name in selected,
                        onClick = { onToggle(name) },
                        label = { Text(name) },
                        leadingIcon = chipLeading?.let { lead -> { lead(name) } },
                        elevation = FilterChipDefaults.filterChipElevation(),
                    )
                }
            }
        }
        if (error != null) {
            Text(
                text = error,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SingleSelectDropdown(
    label: String,
    value: String,
    options: List<String>,
    error: String?,
    onChange: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        ExposedDropdownMenuBox(
            expanded = expanded,
            onExpandedChange = { if (options.isNotEmpty()) expanded = !expanded },
        ) {
            OutlinedTextField(
                colors = PixelTextFieldDefaults.colors(),
                value = value,
                onValueChange = { /* read-only — selection-only field */ },
                readOnly = true,
                label = { Text(label) },
                trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
                isError = error != null,
                supportingText = { error?.let { Text(it) } },
                modifier = Modifier
                    .fillMaxWidth()
                    .menuAnchor(),
            )
            ExposedDropdownMenu(
                expanded = expanded,
                onDismissRequest = { expanded = false },
            ) {
                if (options.isEmpty()) {
                    DropdownMenuItem(
                        text = { Text("No options — refresh from Linear") },
                        onClick = { expanded = false },
                        enabled = false,
                    )
                } else {
                    options.forEach { option ->
                        DropdownMenuItem(
                            text = { Text(option) },
                            onClick = {
                                onChange(option)
                                expanded = false
                            },
                        )
                    }
                }
            }
        }
    }
}

/** Map a Linear state ``type`` to a single-character glyph. The glyphs mirror
 *  the icons Linear's web app uses, but as Unicode characters so the chip
 *  can render the leading slot without pulling in an icon pack. */
private fun stateTypeGlyph(type: String): String = when (type) {
    "backlog" -> "◴"
    "unstarted" -> "○"
    "started" -> "◐"
    "completed" -> "●"
    "canceled" -> "⊘"
    "triage" -> "△"
    else -> "·"
}

/** Fire [onBlur] the first time the modified node loses focus after having been focused. */
private fun Modifier.blurTouchTrigger(onBlur: () -> Unit): Modifier = composed {
    val hadFocus = remember { mutableStateOf(false) }
    onFocusChanged { state ->
        if (state.isFocused) hadFocus.value = true
        else if (hadFocus.value) onBlur()
    }
}
