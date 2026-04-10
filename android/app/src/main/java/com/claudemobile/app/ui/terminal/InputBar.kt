package com.claudemobile.app.ui.terminal

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.outlined.Keyboard
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp

private data class ControlKey(val label: String, val bytes: ByteArray)

private val CONTROL_KEY_ROWS = listOf(
    // Row 1: navigation & editing
    listOf(
        ControlKey("Esc", byteArrayOf(0x1b)),
        ControlKey("Tab", byteArrayOf(0x09)),
        ControlKey("S-Tab", byteArrayOf(0x1b, '['.code.toByte(), 'Z'.code.toByte())),
        ControlKey("Enter", byteArrayOf(0x0d)),
        ControlKey("Ctrl+L", byteArrayOf(0x0c)),
    ),
    // Row 2: signals & arrows
    listOf(
        ControlKey("Ctrl-C", byteArrayOf(0x03)),
        ControlKey("Ctrl-D", byteArrayOf(0x04)),
        ControlKey("\u2191", byteArrayOf(0x1b, '['.code.toByte(), 'A'.code.toByte())),
        ControlKey("\u2193", byteArrayOf(0x1b, '['.code.toByte(), 'B'.code.toByte())),
        ControlKey("\u2190", byteArrayOf(0x1b, '['.code.toByte(), 'D'.code.toByte())),
        ControlKey("\u2192", byteArrayOf(0x1b, '['.code.toByte(), 'C'.code.toByte())),
    ),
)

@Composable
fun InputBar(
    onSend: (String) -> Unit,
    onSendBytes: (ByteArray) -> Unit,
    enabled: Boolean = true,
    modifier: Modifier = Modifier,
) {
    var text by remember { mutableStateOf("") }
    var panelExpanded by remember { mutableStateOf(false) }

    fun send() {
        if (text.isNotEmpty()) {
            onSend(text)
            text = ""
        }
    }

    Column(modifier = modifier.fillMaxWidth()) {
        // Input row — always visible
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Type command...") },
                singleLine = true,
                enabled = enabled,
                textStyle = MaterialTheme.typography.bodyMedium.copy(
                    fontFamily = FontFamily.Monospace,
                ),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { send() }),
            )
            IconButton(
                onClick = { panelExpanded = !panelExpanded },
                enabled = enabled,
            ) {
                Icon(
                    Icons.Outlined.Keyboard,
                    contentDescription = if (panelExpanded) "Hide keys" else "Show keys",
                )
            }
            IconButton(
                onClick = { send() },
                enabled = enabled && text.isNotEmpty(),
            ) {
                Icon(
                    Icons.AutoMirrored.Filled.Send,
                    contentDescription = "Send",
                )
            }
        }

        // Control-key panel — toggles via keyboard icon
        AnimatedVisibility(
            visible = panelExpanded,
            enter = slideInVertically { it },
            exit = slideOutVertically { it },
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 2.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                CONTROL_KEY_ROWS.forEach { row ->
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceEvenly,
                    ) {
                        row.forEach { key ->
                            FilledTonalButton(
                                onClick = { onSendBytes(key.bytes) },
                                enabled = enabled,
                                contentPadding = PaddingValues(
                                    horizontal = 10.dp, vertical = 4.dp,
                                ),
                            ) {
                                Text(
                                    key.label,
                                    style = MaterialTheme.typography.labelMedium.copy(
                                        fontFamily = FontFamily.Monospace,
                                    ),
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}
