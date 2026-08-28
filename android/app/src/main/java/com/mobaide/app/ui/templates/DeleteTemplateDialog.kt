package com.mobaide.app.ui.templates

import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTextButton
import androidx.compose.runtime.Composable

@Composable
fun DeleteTemplateDialog(
    name: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Delete template?") },
        text = { Text("Delete \"$name\"? This cannot be undone.") },
        confirmButton = {
            PixelTextButton(onClick = onConfirm) { Text("Delete") }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}
