package com.mobaide.app.ui.workspaces

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
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
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import com.mobaide.app.ui.common.AutoScanLinearFilterEditor
import com.mobaide.app.ui.common.LinearFilterErrors
import com.mobaide.app.ui.common.pathBrowseHint
import com.mobaide.app.ui.common.PathSuggestionsUi
import com.mobaide.app.ui.common.PathTextField
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
fun WorkspaceFormSheet(
    state: WorkspaceFormState,
    onDismiss: () -> Unit,
    onNameChange: (String) -> Unit,
    onPathChange: (String) -> Unit,
    onCondaChange: (String) -> Unit,
    onLinearKeyChange: (String) -> Unit,
    onToggleKeyVisible: () -> Unit,
    onRequestClearLinearKey: () -> Unit,
    onTouch: (WorkspaceFormState.Field) -> Unit,
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
    pathSuggestions: PathSuggestionsUi = PathSuggestionsUi.None,
    onPathFocusChange: (Boolean) -> Unit = {},
    onPickPathSuggestion: (String) -> Unit = {},
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
                text = if (state.isEdit) "Edit workspace" else "New workspace",
                style = MaterialTheme.typography.titleLarge,
            )

            OutlinedTextField(
                colors = PixelTextFieldDefaults.colors(),
                value = state.name,
                onValueChange = {
                    onNameChange(it)
                    onTouch(WorkspaceFormState.Field.NAME)
                },
                label = { Text("Name") },
                isError = state.nameError != null,
                supportingText = { state.nameError?.let { Text(it) } },
                singleLine = true,
                // Unlike a template name, this one is a constrained identifier —
                // `VALID_NAME` in WorkspaceFormState allows only letters, digits,
                // spaces and `_-.`, so an autocorrected apostrophe or smart quote
                // fails validation outright.
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.None,
                    autoCorrectEnabled = false,
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .blurTouchTrigger { onTouch(WorkspaceFormState.Field.NAME) },
            )

            PathTextField(
                value = state.path,
                onValueChange = {
                    onPathChange(it)
                    onTouch(WorkspaceFormState.Field.PATH)
                },
                suggestions = pathSuggestions,
                onFocusChange = onPathFocusChange,
                onPickSuggestion = {
                    onPickPathSuggestion(it)
                    onTouch(WorkspaceFormState.Field.PATH)
                },
                label = { Text("Path") },
                isError = state.pathError != null,
                supportingText = { Text(state.pathError ?: pathBrowseHint(pathSuggestions.windowsPaths)) },
                modifier = Modifier.blurTouchTrigger { onTouch(WorkspaceFormState.Field.PATH) },
            )

            OutlinedTextField(
                colors = PixelTextFieldDefaults.colors(),
                value = state.condaEnv,
                onValueChange = {
                    onCondaChange(it)
                    onTouch(WorkspaceFormState.Field.CONDA)
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
                    onTouch(WorkspaceFormState.Field.KEY)
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
                val gitWorkspace = state.editing
                if (gitWorkspace?.is_git == true) {
                    PixelTextButton(onClick = { onOpenGit(gitWorkspace.name) }) { Text("Git") }
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

@Composable
private fun AutoScanFilterSection(
    state: WorkspaceFormState,
    onToggleTriggerLabel: (String) -> Unit,
    onToggleTriggerState: (String) -> Unit,
    onActiveStateChange: (String) -> Unit,
    onDoneStateChange: (String) -> Unit,
    onRemovalLabelChange: (String) -> Unit,
    onRefresh: () -> Unit,
) {
    AutoScanLinearFilterEditor(
        availableLabels = state.availableLabels,
        availableStates = state.availableStates,
        triggerLabels = state.triggerLabels,
        triggerStates = state.triggerStates,
        activeState = state.activeState,
        doneState = state.doneState,
        removalLabel = state.removalLabel,
        loading = state.filterMetadataLoading,
        loadError = state.filterMetadataError,
        errors = LinearFilterErrors(
            triggerLabels = state.triggerLabelsError,
            triggerStates = state.triggerStatesError,
            activeState = state.activeStateError,
            doneState = state.doneStateError,
            removalLabel = state.removalLabelError,
        ),
        onToggleTriggerLabel = onToggleTriggerLabel,
        onToggleTriggerState = onToggleTriggerState,
        onActiveStateChange = onActiveStateChange,
        onDoneStateChange = onDoneStateChange,
        onRemovalLabelChange = onRemovalLabelChange,
        onRefresh = onRefresh,
    )
}

/** Fire [onBlur] the first time the modified node loses focus after having been focused. */
private fun Modifier.blurTouchTrigger(onBlur: () -> Unit): Modifier = composed {
    val hadFocus = remember { mutableStateOf(false) }
    onFocusChanged { state ->
        if (state.isFocused) hadFocus.value = true
        else if (hadFocus.value) onBlur()
    }
}
