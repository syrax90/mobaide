package com.mobaide.app.ui.terminal

import android.content.res.Configuration
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.LibraryBooks
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.BookmarkAdd
import androidx.compose.material.icons.filled.OpenInFull
import androidx.compose.material.icons.outlined.Keyboard
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.SessionKind

private data class ControlKey(val label: String, val bytes: ByteArray)

private val EscKey = ControlKey("Esc", byteArrayOf(0x1b))
private val TabKey = ControlKey("Tab", byteArrayOf(0x09))
private val ShiftTabKey = ControlKey("S+Tab", byteArrayOf(0x1b, '['.code.toByte(), 'Z'.code.toByte()))
private val EnterKey = ControlKey("Enter", byteArrayOf(0x0d))
private val CtrlCKey = ControlKey("Ctrl+C", byteArrayOf(0x03))
private val UpKey = ControlKey("↑", byteArrayOf(0x1b, '['.code.toByte(), 'A'.code.toByte()))
private val DownKey = ControlKey("↓", byteArrayOf(0x1b, '['.code.toByte(), 'B'.code.toByte()))
private val LeftKey = ControlKey("←", byteArrayOf(0x1b, '['.code.toByte(), 'D'.code.toByte()))
private val RightKey = ControlKey("→", byteArrayOf(0x1b, '['.code.toByte(), 'C'.code.toByte()))

private val CLAUDE_CONTROL_KEYS = listOf(
    EscKey, TabKey, ShiftTabKey, EnterKey, CtrlCKey, UpKey, DownKey, LeftKey, RightKey,
)

// Codex-first keys: Approve/Reject answer the frequent approval prompts (bare 'y'/'n',
// no CR — Enter stays a separate key); Back/Edit is the Esc-Esc backtrack; Newline
// (Ctrl+J, 0x0a) inserts a newline the server's delayed-CR submit path cannot. Then the
// shared keys, then power-user extras (Ctrl+L clear / Ctrl+O copy-output / Ctrl+R history).
private val CODEX_CONTROL_KEYS = listOf(
    ControlKey("Approve", byteArrayOf('y'.code.toByte())),
    ControlKey("Reject", byteArrayOf('n'.code.toByte())),
    ControlKey("Back/Edit", byteArrayOf(0x1b, 0x1b)),
    ControlKey("Newline", byteArrayOf(0x0a)),
    EscKey, EnterKey, CtrlCKey, TabKey,
    UpKey, DownKey, LeftKey, RightKey,
    ControlKey("Ctrl+L", byteArrayOf(0x0c)),
    ControlKey("Ctrl+O", byteArrayOf(0x0f)),
    ControlKey("Ctrl+R", byteArrayOf(0x12)),
)

private fun controlKeysFor(kind: SessionKind): List<ControlKey> = when (kind) {
    SessionKind.Codex -> CODEX_CONTROL_KEYS
    else -> CLAUDE_CONTROL_KEYS // Claude, shell and auto-scan share the original set.
}

@Composable
fun InputBar(
    text: TextFieldValue,
    onTextChange: (TextFieldValue) -> Unit,
    onSend: (String) -> Unit,
    onSendBytes: (ByteArray) -> Unit,
    enabled: Boolean = true,
    requestFocus: Boolean = false,
    onFocusHandled: () -> Unit = {},
    onExpand: () -> Unit = {},
    onOpenTemplates: () -> Unit = {},
    onSaveAsTemplate: () -> Unit = {},
    sessionKind: SessionKind = SessionKind.Claude,
    modifier: Modifier = Modifier,
) {
    val haptic = LocalHapticFeedback.current
    var panelExpanded by remember { mutableStateOf(false) }
    var hasFocus by remember { mutableStateOf(false) }
    val focusRequester = remember { FocusRequester() }
    val controlKeys = remember(sessionKind) { controlKeysFor(sessionKind) }
    val isLandscape = LocalConfiguration.current.orientation ==
        Configuration.ORIENTATION_LANDSCAPE

    LaunchedEffect(requestFocus) {
        if (requestFocus) {
            focusRequester.requestFocus()
            onFocusHandled()
        }
    }

    fun send() {
        val trimmed = text.text.trimEnd('\n', '\r')
        if (trimmed.isNotEmpty()) {
            onSend(trimmed)
            onTextChange(TextFieldValue(""))
        }
    }

    Column(modifier = modifier.fillMaxWidth()) {
        if (isLandscape) {
            // Landscape: text field and action buttons share one row.
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                InputField(
                    text = text,
                    onTextChange = onTextChange,
                    enabled = enabled,
                    focusRequester = focusRequester,
                    hasFocus = hasFocus,
                    onHasFocusChange = { hasFocus = it },
                    modifier = Modifier.weight(1f),
                )
                ActionButtons(
                    enabled = enabled,
                    canSend = text.text.isNotEmpty(),
                    panelExpanded = panelExpanded,
                    onOpenTemplates = onOpenTemplates,
                    onSaveAsTemplate = onSaveAsTemplate,
                    onTogglePanel = { panelExpanded = !panelExpanded },
                    onExpand = onExpand,
                    onSend = { send() },
                )
            }
        } else {
            // Portrait: text field spans full width above the action buttons.
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 4.dp),
            ) {
                InputField(
                    text = text,
                    onTextChange = onTextChange,
                    enabled = enabled,
                    focusRequester = focusRequester,
                    hasFocus = hasFocus,
                    onHasFocusChange = { hasFocus = it },
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    ActionButtons(
                        enabled = enabled,
                        canSend = text.text.isNotEmpty(),
                        panelExpanded = panelExpanded,
                        onOpenTemplates = onOpenTemplates,
                        onSaveAsTemplate = onSaveAsTemplate,
                        onTogglePanel = { panelExpanded = !panelExpanded },
                        onExpand = onExpand,
                        onSend = { send() },
                    )
                }
            }
        }

        // Control-key panel — single horizontally scrollable row.
        AnimatedVisibility(
            visible = panelExpanded,
            enter = slideInVertically { it },
            exit = slideOutVertically { it },
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState())
                    .padding(horizontal = 8.dp, vertical = 2.dp),
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                controlKeys.forEach { key ->
                    FilledTonalButton(
                        onClick = {
                            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                            onSendBytes(key.bytes)
                        },
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

@Composable
private fun InputField(
    text: TextFieldValue,
    onTextChange: (TextFieldValue) -> Unit,
    enabled: Boolean,
    focusRequester: FocusRequester,
    hasFocus: Boolean,
    onHasFocusChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    OutlinedTextField(
        value = text,
        onValueChange = { onTextChange(it) },
        modifier = modifier
            .focusRequester(focusRequester)
            .onFocusChanged { focusState ->
                // Auto-place cursor at end on focus only when the
                // current selection is collapsed (a cursor). When
                // upstream code (e.g. the templates picker) has set
                // a non-collapsed range like select-all, preserve it.
                if (focusState.isFocused && !hasFocus
                    && text.text.isNotEmpty()
                    && text.selection.collapsed) {
                    onTextChange(text.copy(selection = TextRange(text.text.length)))
                }
                onHasFocusChange(focusState.isFocused)
            },
        placeholder = { Text("Type command...") },
        minLines = 1,
        maxLines = 10,
        enabled = enabled,
        textStyle = MaterialTheme.typography.bodyMedium.copy(
            fontFamily = FontFamily.Monospace,
        ),
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Default),
    )
}

@Composable
private fun ActionButtons(
    enabled: Boolean,
    canSend: Boolean,
    panelExpanded: Boolean,
    onOpenTemplates: () -> Unit,
    onSaveAsTemplate: () -> Unit,
    onTogglePanel: () -> Unit,
    onExpand: () -> Unit,
    onSend: () -> Unit,
) {
    IconButton(onClick = onOpenTemplates) {
        Icon(
            Icons.AutoMirrored.Filled.LibraryBooks,
            contentDescription = "Templates",
        )
    }
    IconButton(
        onClick = onSaveAsTemplate,
        enabled = canSend,
    ) {
        Icon(
            Icons.Default.BookmarkAdd,
            contentDescription = "Save as template",
        )
    }
    IconButton(
        onClick = onTogglePanel,
        enabled = enabled,
    ) {
        Icon(
            Icons.Outlined.Keyboard,
            contentDescription = if (panelExpanded) "Hide keys" else "Show keys",
        )
    }
    IconButton(
        onClick = onExpand,
        enabled = enabled,
    ) {
        Icon(
            Icons.Default.OpenInFull,
            contentDescription = "Expand editor",
        )
    }
    IconButton(
        onClick = onSend,
        enabled = enabled && canSend,
    ) {
        Icon(
            Icons.AutoMirrored.Filled.Send,
            contentDescription = "Send",
        )
    }
}
