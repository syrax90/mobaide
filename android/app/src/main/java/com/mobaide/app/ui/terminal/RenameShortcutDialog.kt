package com.mobaide.app.ui.terminal

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp

/**
 * Rename a saved shortcut button. Confirming with a blank name clears the
 * custom name, so the button falls back to the chord label (e.g. "Ctrl+X").
 */
@Composable
fun RenameShortcutDialog(
    shortcut: SavedShortcutUi,
    onRename: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var name by remember { mutableStateOf(shortcut.name.orEmpty()) }
    val focusRequester = remember { FocusRequester() }
    LaunchedEffect(Unit) {
        // requestFocus() throws if the field's node is not attached yet, which
        // depends on whether the dialog window attaches before this effect
        // resumes — order this dialog does not control now that the shortcut
        // picker opens it over an already-open ModalBottomSheet. Autofocus is a
        // convenience; the user can always tap the field, so it must not be
        // able to take the dialog down.
        try {
            focusRequester.requestFocus()
        } catch (_: IllegalStateException) {
        }
    }

    PixelAlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Rename shortcut") },
        text = {
            Column {
                Text(
                    text = shortcut.chord.display(),
                    style = MaterialTheme.typography.bodySmall.copy(
                        fontFamily = FontFamily.Monospace,
                    ),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(bottom = 8.dp),
                )
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it.take(MAX_SHORTCUT_NAME_LENGTH) },
                    modifier = Modifier
                        .fillMaxWidth()
                        .focusRequester(focusRequester),
                    placeholder = { Text("Button name") },
                    supportingText = {
                        Text("Leave empty to show the shortcut itself")
                    },
                    singleLine = true,
                )
            }
        },
        confirmButton = {
            PixelTextButton(onClick = { onRename(name) }) { Text("Rename") }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}
