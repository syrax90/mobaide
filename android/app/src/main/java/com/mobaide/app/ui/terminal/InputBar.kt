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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.LibraryBooks
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Backspace
import androidx.compose.material.icons.filled.BookmarkAdd
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowLeft
import androidx.compose.material.icons.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.OpenInFull
import androidx.compose.material.icons.outlined.Keyboard
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RichTooltip
import androidx.compose.material3.Text
import androidx.compose.material3.TooltipBox
import androidx.compose.material3.TooltipDefaults
import androidx.compose.material3.rememberTooltipState
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
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.ui.theme.MobaideTheme

private data class ControlKey(
    val label: String,
    val bytes: ByteArray,
    val icon: ImageVector? = null,
)

private val EscKey = ControlKey("Esc", byteArrayOf(0x1b))
private val TabKey = ControlKey("Tab", byteArrayOf(0x09))
// DEL (0x7f) is the terminal erase byte readline/Claude Code/Codex expect. Direct-input
// mode has no composer buffer to delete from, and soft keyboards drive delete
// inconsistently on a bufferless InputConnection — an explicit key is the reliable path.
// Non-mirrored for the same reason as Left/Right below: the byte always erases leftwards.
@Suppress("DEPRECATION")
private val BackspaceKey = ControlKey("Backspace", byteArrayOf(0x7f), Icons.Filled.Backspace)
private val ModeKey = ControlKey("Mode", byteArrayOf(0x1b, '['.code.toByte(), 'Z'.code.toByte()))
private val RewindKey = ControlKey("Rewind", byteArrayOf(0x1b, 0x1b))
private val EnterKey = ControlKey("Enter", byteArrayOf(0x0d))
private val CtrlCKey = ControlKey("Ctrl+C", byteArrayOf(0x03))
private val UpKey = ControlKey("↑", byteArrayOf(0x1b, '['.code.toByte(), 'A'.code.toByte()), Icons.Filled.KeyboardArrowUp)
private val DownKey = ControlKey("↓", byteArrayOf(0x1b, '['.code.toByte(), 'B'.code.toByte()), Icons.Filled.KeyboardArrowDown)
// Non-mirrored Left/Right: a terminal cursor key must point the way it moves the
// cursor. AutoMirrored would flip the glyph in RTL while the bytes (ESC[D / ESC[C)
// stay fixed, leaving the arrow pointing the wrong way.
@Suppress("DEPRECATION")
private val LeftKey = ControlKey("←", byteArrayOf(0x1b, '['.code.toByte(), 'D'.code.toByte()), Icons.Filled.KeyboardArrowLeft)
@Suppress("DEPRECATION")
private val RightKey = ControlKey("→", byteArrayOf(0x1b, '['.code.toByte(), 'C'.code.toByte()), Icons.Filled.KeyboardArrowRight)
private val CtrlOKey = ControlKey("Ctrl+O", byteArrayOf(0x0f))
private val CtrlRKey = ControlKey("Ctrl+R", byteArrayOf(0x12))
private val CtrlLKey = ControlKey("Ctrl+L", byteArrayOf(0x0c))
private val ApproveKey = ControlKey("Approve", byteArrayOf('y'.code.toByte()))
private val RejectKey = ControlKey("Reject", byteArrayOf('n'.code.toByte()))
// Antigravity (agy) TUI controls: Ctrl+K fast-approves the pending action,
// Ctrl+G opens the tool-confirmation view, Ctrl+D exits the CLI.
private val CtrlKKey = ControlKey("Ctrl+K", byteArrayOf(0x0b))
private val CtrlGKey = ControlKey("Ctrl+G", byteArrayOf(0x07))
private val CtrlDKey = ControlKey("Ctrl+D", byteArrayOf(0x04))

// Nav/control tail shared by every CLI. Arrows + Enter drive the in-terminal menus (mode
// picker, approval/selection prompts, history); Ctrl+O toggles the transcript, Ctrl+R
// searches history; Backspace erases characters typed straight into a TUI (direct-input
// mode has no composer buffer to delete from).
private val SHARED_TAIL = listOf(
    EscKey, CtrlCKey, EnterKey, TabKey, BackspaceKey,
    UpKey, DownKey, LeftKey, RightKey,
    CtrlOKey, CtrlRKey,
)

// Claude / shell / auto-scan: Mode (Shift+Tab, CSI Z — cycles plan/accept-edits/normal) and
// Rewind (Esc-Esc — edits the previous message) lead the shared tail; Ctrl+L (clear screen)
// closes the set.
private val CLAUDE_CONTROL_KEYS = listOf(ModeKey, RewindKey) + SHARED_TAIL + CtrlLKey

// Codex = the Claude set plus its high-frequency approval answers (bare 'y'/'n', no CR —
// Enter stays a separate key) up front. Ctrl+L arrives via the embedded Claude set.
private val CODEX_CONTROL_KEYS = listOf(ApproveKey, RejectKey) + CLAUDE_CONTROL_KEYS

// Antigravity (agy) leads with its own TUI controls — Ctrl+K (fast-approve pending action),
// Ctrl+G (tool confirmation view), Ctrl+D (exit) — then the shared tail and Ctrl+L
// (clear screen). Bracketed paste is honored, so no newline key.
private val ANTIGRAVITY_CONTROL_KEYS =
    listOf(CtrlKKey, CtrlGKey, CtrlDKey) + SHARED_TAIL + CtrlLKey

private fun controlKeysFor(kind: SessionKind): List<ControlKey> = when (kind) {
    SessionKind.Codex -> CODEX_CONTROL_KEYS
    SessionKind.Antigravity -> ANTIGRAVITY_CONTROL_KEYS
    else -> CLAUDE_CONTROL_KEYS // Claude, shell, and auto-scan share the Mode/nav/control core.
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
    composerVisible: Boolean = true,
    onRevealComposer: () -> Unit = {},
    showCoachMark: Boolean = false,
    onCoachMarkDismissed: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    val haptic = LocalHapticFeedback.current
    val keyboardController = LocalSoftwareKeyboardController.current
    var panelExpanded by remember { mutableStateOf(false) }
    var hasFocus by remember { mutableStateOf(false) }
    val focusRequester = remember { FocusRequester() }
    val controlKeys = remember(sessionKind) { controlKeysFor(sessionKind) }
    val isLandscape = LocalConfiguration.current.orientation ==
        Configuration.ORIENTATION_LANDSCAPE

    // Gated on composerVisible: the ViewModel reveals the composer in the same state
    // update that requests focus, but the FocusRequester throws if the field is not
    // composed yet — keying on both defers the request to the frame that mounts it.
    // The explicit show() covers IMEs that don't raise on programmatic focus alone.
    LaunchedEffect(requestFocus, composerVisible) {
        if (requestFocus && composerVisible) {
            focusRequester.requestFocus()
            keyboardController?.show()
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
        // Composer block (field + action buttons). Auto-hidden in direct-input mode —
        // the terminal owns typing there — and restored via the reveal chip below.
        AnimatedVisibility(
            visible = composerVisible,
            enter = slideInVertically { it },
            exit = slideOutVertically { it },
        ) {
            ComposerBlock(
                isLandscape = isLandscape,
                text = text,
                onTextChange = onTextChange,
                enabled = enabled,
                focusRequester = focusRequester,
                hasFocus = hasFocus,
                onHasFocusChange = { hasFocus = it },
                panelExpanded = panelExpanded,
                onOpenTemplates = onOpenTemplates,
                onSaveAsTemplate = onSaveAsTemplate,
                onTogglePanel = { panelExpanded = !panelExpanded },
                onExpand = onExpand,
                onSend = { send() },
            )
        }

        // Control-key panel — single horizontally scrollable row; forced visible while
        // the composer is hidden so direct-input mode always keeps Esc/arrows/Backspace
        // and the reveal chip reachable.
        AnimatedVisibility(
            visible = panelExpanded || !composerVisible,
            enter = slideInVertically { it },
            exit = slideOutVertically { it },
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 2.dp),
                horizontalArrangement = Arrangement.spacedBy(4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (!composerVisible) {
                    RevealComposerChip(
                        showCoachMark = showCoachMark,
                        onCoachMarkDismissed = onCoachMarkDismissed,
                        onClick = {
                            panelExpanded = true
                            onRevealComposer()
                        },
                    )
                }
                Row(
                    modifier = Modifier
                        .weight(1f)
                        .horizontalScroll(rememberScrollState()),
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
                            val icon = key.icon
                            if (icon != null) {
                                Icon(
                                    icon,
                                    contentDescription = key.label,
                                    modifier = Modifier.size(18.dp),
                                )
                            } else {
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

@Composable
private fun ComposerBlock(
    isLandscape: Boolean,
    text: TextFieldValue,
    onTextChange: (TextFieldValue) -> Unit,
    enabled: Boolean,
    focusRequester: FocusRequester,
    hasFocus: Boolean,
    onHasFocusChange: (Boolean) -> Unit,
    panelExpanded: Boolean,
    onOpenTemplates: () -> Unit,
    onSaveAsTemplate: () -> Unit,
    onTogglePanel: () -> Unit,
    onExpand: () -> Unit,
    onSend: () -> Unit,
) {
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
                onHasFocusChange = onHasFocusChange,
                modifier = Modifier.weight(1f),
            )
            ActionButtons(
                enabled = enabled,
                canSend = text.text.isNotEmpty(),
                panelExpanded = panelExpanded,
                onOpenTemplates = onOpenTemplates,
                onSaveAsTemplate = onSaveAsTemplate,
                onTogglePanel = onTogglePanel,
                onExpand = onExpand,
                onSend = onSend,
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
                onHasFocusChange = onHasFocusChange,
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
                    onTogglePanel = onTogglePanel,
                    onExpand = onExpand,
                    onSend = onSend,
                )
            }
        }
    }
}

/** Labeled affordance that restores the auto-hidden composer; hosts the one-time
 *  coach mark teaching the hide/reveal mechanic on the first auto-hide. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun RevealComposerChip(
    showCoachMark: Boolean,
    onCoachMarkDismissed: () -> Unit,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val tooltipState = rememberTooltipState(isPersistent = true)
    LaunchedEffect(showCoachMark) {
        if (showCoachMark) {
            // show() suspends until the tooltip is dismissed (tap outside / back).
            tooltipState.show()
            onCoachMarkDismissed()
        }
    }
    TooltipBox(
        positionProvider = TooltipDefaults.rememberRichTooltipPositionProvider(),
        state = tooltipState,
        tooltip = {
            RichTooltip(
                title = { Text("Command field is tucked away") },
            ) {
                Text(
                    "Typing goes straight into the terminal now. " +
                        "Tap here to bring the command field back.",
                )
            }
        },
        modifier = modifier,
    ) {
        AssistChip(
            onClick = onClick,
            label = { Text("Command field") },
            leadingIcon = {
                Icon(
                    Icons.Outlined.Keyboard,
                    contentDescription = null,
                    modifier = Modifier.size(AssistChipDefaults.IconSize),
                )
            },
            modifier = Modifier.testTag("reveal_composer_chip"),
        )
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

@Preview(name = "InputBar light", showBackground = true)
@Preview(
    name = "InputBar dark",
    showBackground = true,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun InputBarPreview() {
    MobaideTheme(dynamicColor = false) {
        InputBar(
            text = TextFieldValue(""),
            onTextChange = {},
            onSend = {},
            onSendBytes = {},
        )
    }
}

@Preview(name = "InputBar direct mode (composer hidden) light", showBackground = true)
@Preview(
    name = "InputBar direct mode (composer hidden) dark",
    showBackground = true,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun InputBarComposerHiddenPreview() {
    MobaideTheme(dynamicColor = false) {
        InputBar(
            text = TextFieldValue(""),
            onTextChange = {},
            onSend = {},
            onSendBytes = {},
            composerVisible = false,
        )
    }
}
