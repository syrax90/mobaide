package com.mobaide.app.ui.sessions

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import androidx.compose.material3.ModalBottomSheetProperties
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SheetValue
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.ui.theme.MobaideTheme

/**
 * Bottom-sheet editor for an account-scoped custom AI agent type. A null
 * [target] creates a new type; otherwise the sheet edits it in place.
 *
 * Drafts are local, keyed on the target's identity, and survive rotation via
 * [rememberSaveable]. Validation failures and server rejections (20-type cap,
 * name conflict — both 409s) arrive via [error] and render inline; the caller
 * dismisses the sheet only on a successful save.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CustomAgentTypeEditorSheet(
    target: CustomAgentTypeDto?,
    error: String?,
    saving: Boolean,
    onSave: (name: String, command: String) -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var name by rememberSaveable(target?.id) { mutableStateOf(target?.name ?: "") }
    var command by rememberSaveable(target?.id) { mutableStateOf(target?.command ?: "") }
    // Scrim tap / back / swipe must not dismiss mid-save: dismissal resets the
    // ViewModel's saving state, which would let a second save start while the
    // first is in flight and let the first save's completion close (or
    // mislabel the error of) a subsequently reopened editor. The veto must
    // happen in confirmValueChange — Material3 hides the sheet BEFORE calling
    // onDismissRequest, so gating only the callback would leave an invisible
    // but still-composed sheet blocking the screen. Back-press bypasses
    // confirmValueChange entirely (it calls hide() directly), hence the
    // properties gate below. savingState is read through rememberUpdatedState
    // so the confirmValueChange lambda stays identity-stable — it is a
    // rememberSaveable key of the sheet state, and a new lambda per `saving`
    // flip would recreate the sheet mid-animation.
    val savingState = rememberUpdatedState(saving)
    val sheetState = rememberModalBottomSheetState(
        skipPartiallyExpanded = true,
        confirmValueChange = { value ->
            value != SheetValue.Hidden || !savingState.value
        },
    )

    PixelBottomSheet(
        onDismissRequest = { if (!savingState.value) onDismiss() },
        sheetState = sheetState,
        properties = ModalBottomSheetProperties(shouldDismissOnBackPress = !saving),
        modifier = modifier,
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
                text = if (target == null) "New custom agent" else "Edit custom agent",
                style = MaterialTheme.typography.titleLarge,
            )

            OutlinedTextField(
                value = name,
                onValueChange = { name = it },
                label = { Text("Name") },
                singleLine = true,
                enabled = !saving,
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("custom_type_name_field"),
            )

            // Keyboard options are hints only — the ViewModel still trims and
            // validates on save, and the server enforces the real limits.
            OutlinedTextField(
                value = command,
                onValueChange = { command = it },
                label = { Text("Launch command") },
                placeholder = { Text("e.g. aider --model sonnet") },
                textStyle = TextStyle(fontFamily = FontFamily.Monospace),
                minLines = 3,
                enabled = !saving,
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.None,
                    autoCorrectEnabled = false,
                    keyboardType = KeyboardType.Ascii,
                ),
                supportingText = {
                    Text("Runs in your login shell inside the session directory")
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("custom_type_command_field"),
            )

            error?.let {
                Text(
                    text = it,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.testTag("custom_type_editor_error"),
                )
            }

            PixelButton(
                style = PixelButtonStyle.Primary,
                onClick = { onSave(name.trim(), command.trim()) },
                enabled = !saving && name.isNotBlank() && command.isNotBlank(),
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("custom_type_save_button"),
            ) {
                Text(if (saving) "Saving…" else "Save")
            }

            PixelTextButton(
                onClick = onDismiss,
                enabled = !saving,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Cancel")
            }
        }
    }
}

@Preview(name = "Create, light", showBackground = true)
@Composable
private fun CustomAgentTypeEditorSheetCreatePreview() {
    MobaideTheme(darkTheme = false) {
        CustomAgentTypeEditorSheet(
            target = null,
            error = null,
            saving = false,
            onSave = { _, _ -> },
            onDismiss = {},
        )
    }
}

@Preview(name = "Edit with error, dark", showBackground = true)
@Composable
private fun CustomAgentTypeEditorSheetEditPreview() {
    MobaideTheme(darkTheme = true) {
        CustomAgentTypeEditorSheet(
            target = CustomAgentTypeDto(
                id = "ab12cd34ef56",
                name = "Aider",
                command = "aider --model sonnet",
                created_at = "2026-07-28T00:00:00+00:00",
                updated_at = "2026-07-28T00:00:00+00:00",
            ),
            error = "A custom agent type with this name already exists",
            saving = false,
            onSave = { _, _ -> },
            onDismiss = {},
        )
    }
}
