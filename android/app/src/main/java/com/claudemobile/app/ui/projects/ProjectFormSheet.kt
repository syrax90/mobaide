package com.claudemobile.app.ui.projects

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
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.focus.onFocusChanged
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
    onSave: () -> Unit,
    onOpenGit: (String) -> Unit = {},
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(
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
                value = state.name,
                onValueChange = {
                    onNameChange(it)
                    onTouch(ProjectFormState.Field.NAME)
                },
                label = { Text("Name") },
                isError = state.nameError != null,
                supportingText = { state.nameError?.let { Text(it) } },
                singleLine = true,
                modifier = Modifier
                    .fillMaxWidth()
                    .blurTouchTrigger { onTouch(ProjectFormState.Field.NAME) },
            )

            OutlinedTextField(
                value = state.path,
                onValueChange = {
                    onPathChange(it)
                    onTouch(ProjectFormState.Field.PATH)
                },
                label = { Text("Path") },
                isError = state.pathError != null,
                supportingText = { state.pathError?.let { Text(it) } },
                singleLine = true,
                modifier = Modifier
                    .fillMaxWidth()
                    .blurTouchTrigger { onTouch(ProjectFormState.Field.PATH) },
            )

            OutlinedTextField(
                value = state.condaEnv,
                onValueChange = {
                    onCondaChange(it)
                    onTouch(ProjectFormState.Field.CONDA)
                },
                label = { Text("Conda env (optional)") },
                singleLine = true,
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

            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End,
            ) {
                val gitProject = state.editing
                if (gitProject?.is_git == true) {
                    TextButton(onClick = { onOpenGit(gitProject.name) }) { Text("Git") }
                    Spacer(Modifier.width(8.dp))
                }
                TextButton(onClick = onDismiss) { Text("Cancel") }
                Spacer(Modifier.width(8.dp))
                Button(
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
                TextButton(onClick = onClearRequested, enabled = !clearRequested) {
                    Text(if (clearRequested) "Will clear" else "Clear key")
                }
            }
        }
    }
}

/** Fire [onBlur] the first time the modified node loses focus after having been focused. */
private fun Modifier.blurTouchTrigger(onBlur: () -> Unit): Modifier = composed {
    val hadFocus = remember { mutableStateOf(false) }
    onFocusChanged { state ->
        if (state.isFocused) hadFocus.value = true
        else if (hadFocus.value) onBlur()
    }
}
