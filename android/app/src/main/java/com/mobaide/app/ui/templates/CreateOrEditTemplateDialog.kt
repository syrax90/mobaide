package com.mobaide.app.ui.templates

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
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

    AlertDialog(
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
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onSave(name.trim(), body) },
                enabled = name.trim().isNotEmpty() && body.isNotEmpty(),
            ) { Text("Save") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}
