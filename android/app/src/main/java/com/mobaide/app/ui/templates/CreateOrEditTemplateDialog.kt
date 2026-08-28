package com.mobaide.app.ui.templates

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.TemplateDto

@Composable
fun CreateOrEditTemplateDialog(
    target: TemplateDto?,
    onSave: (name: String, body: String) -> Unit,
    onDismiss: () -> Unit,
) {
    var name by remember(target?.template_id) { mutableStateOf(target?.name ?: "") }
    var body by remember(target?.template_id) { mutableStateOf(target?.body ?: "") }

    PixelAlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (target == null) "New template" else "Edit template") },
        text = {
            Column {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    modifier = Modifier.fillMaxWidth(),
                    placeholder = { Text("Name") },
                    singleLine = true,
                )
                OutlinedTextField(
                    value = body,
                    onValueChange = { body = it },
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 120.dp, max = 320.dp)
                        .padding(top = 8.dp),
                    placeholder = { Text("Prompt body") },
                    textStyle = MaterialTheme.typography.bodyMedium.copy(
                        fontFamily = FontFamily.Monospace,
                    ),
                    // Saved bodies are replayed into the terminal composer and
                    // sent to the PTY unchanged, so they get the same
                    // verbatim-input rule. The name field above is a display
                    // label and keeps the system defaults.
                    keyboardOptions = KeyboardOptions(
                        capitalization = KeyboardCapitalization.None,
                        autoCorrectEnabled = false,
                    ),
                )
            }
        },
        confirmButton = {
            PixelTextButton(
                onClick = { onSave(name.trim(), body) },
                enabled = name.trim().isNotEmpty() && body.isNotEmpty(),
            ) { Text("Save") }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}
