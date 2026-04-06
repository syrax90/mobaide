package com.claudemobile.app.ui.sessions

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp

@Composable
fun CreateSessionDialog(
    onDismiss: () -> Unit,
    onCreate: (command: String?, cwd: String?, rows: Int, cols: Int) -> Unit,
) {
    var command by remember { mutableStateOf("") }
    var cwd by remember { mutableStateOf("") }
    var rows by remember { mutableStateOf("24") }
    var cols by remember { mutableStateOf("80") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New Session") },
        text = {
            Column {
                OutlinedTextField(
                    value = command,
                    onValueChange = { command = it },
                    label = { Text("Command (optional)") },
                    placeholder = { Text("Default: system shell") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(modifier = Modifier.height(8.dp))
                OutlinedTextField(
                    value = cwd,
                    onValueChange = { cwd = it },
                    label = { Text("Working Directory (optional)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(modifier = Modifier.height(8.dp))
                OutlinedTextField(
                    value = rows,
                    onValueChange = { rows = it },
                    label = { Text("Rows") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth(0.5f),
                )
                Spacer(modifier = Modifier.height(8.dp))
                OutlinedTextField(
                    value = cols,
                    onValueChange = { cols = it },
                    label = { Text("Columns") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth(0.5f),
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    onCreate(
                        command.ifBlank { null },
                        cwd.ifBlank { null },
                        rows.toIntOrNull() ?: 24,
                        cols.toIntOrNull() ?: 80,
                    )
                },
            ) {
                Text("Create")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Cancel")
            }
        },
    )
}
