package com.mobaide.app.ui.terminal

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
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
import com.mobaide.app.ui.theme.ChromeFontFamily
import androidx.compose.ui.unit.dp

@Composable
fun SaveTemplateDialog(
    bodyPreview: String,
    onSave: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var name by remember { mutableStateOf("") }
    val focusRequester = remember { FocusRequester() }
    LaunchedEffect(Unit) { focusRequester.requestFocus() }

    PixelAlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Save as template") },
        text = {
            Column {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    modifier = Modifier
                        .fillMaxWidth()
                        .focusRequester(focusRequester),
                    placeholder = { Text("Template name") },
                    singleLine = true,
                )
                Text(
                    text = "Body",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 12.dp, bottom = 4.dp),
                )
                Text(
                    text = bodyPreview,
                    style = MaterialTheme.typography.bodySmall.copy(
                        fontFamily = ChromeFontFamily,
                    ),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(max = 160.dp),
                )
            }
        },
        confirmButton = {
            PixelTextButton(
                onClick = { onSave(name) },
                enabled = name.trim().isNotEmpty() && bodyPreview.isNotEmpty(),
            ) { Text("Save") }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}
