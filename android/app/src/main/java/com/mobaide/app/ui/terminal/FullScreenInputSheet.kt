package com.mobaide.app.ui.terminal

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import com.mobaide.app.ui.common.pixel.PixelIconButton
import androidx.compose.material3.OutlinedTextField
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FullScreenInputSheet(
    text: TextFieldValue,
    onTextChange: (TextFieldValue) -> Unit,
    onSend: (String) -> Unit,
    onDismiss: () -> Unit,
    enabled: Boolean = true,
    micButton: (@Composable () -> Unit)? = null,
    dictationBar: (@Composable () -> Unit)? = null,
) {
    val focusRequester = remember { FocusRequester() }

    PixelBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .imePadding(),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                PixelIconButton(
                    imageVector = Icons.Default.Close,
                    contentDescription = "Collapse editor",
                    onClick = onDismiss,
                )
                Row(
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    if (dictationBar == null) micButton?.invoke()
                    PixelIconButton(
                        imageVector = Icons.AutoMirrored.Filled.Send,
                        contentDescription = "Send",
                        onClick = {
                            val trimmed = text.text.trimEnd('\n', '\r')
                            if (trimmed.isNotEmpty()) onSend(trimmed)
                        },
                        enabled = enabled && text.text.isNotEmpty() && dictationBar == null,
                    )
                }
            }
            if (dictationBar != null) {
                Box(modifier = Modifier.padding(horizontal = 8.dp)) { dictationBar() }
            }
            OutlinedTextField(
                colors = PixelTextFieldDefaults.colors(),
                value = text,
                onValueChange = onTextChange,
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
                    .padding(horizontal = 16.dp, vertical = 8.dp)
                    .focusRequester(focusRequester),
                placeholder = { Text("Compose your prompt...") },
                enabled = enabled,
                textStyle = MaterialTheme.typography.bodyMedium,
                // Same verbatim-input rule as the inline composer this sheet
                // expands. `imeAction` is left unset so the multi-line default
                // (newline, not send) is preserved.
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.None,
                    autoCorrectEnabled = false,
                ),
            )
        }
    }

    LaunchedEffect(Unit) {
        focusRequester.requestFocus()
    }
}
