package com.mobaide.app.ui.terminal

import android.content.res.Configuration
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
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
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowLeft
import androidx.compose.material.icons.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.KeyboardHide
import androidx.compose.material.icons.filled.OpenInFull
import androidx.compose.material.icons.outlined.Keyboard
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import com.mobaide.app.ui.common.pixel.PixelButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
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
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
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

// Canonical left-to-right order for every control key. Each session kind renders only the
// subset it supports (see the membership lists below), filtered from this single list, so the
// button order stays consistent across kinds — only which buttons appear differs. The high-
// frequency keys lead (Enter, arrows, Ctrl+C, Mode, Ctrl+L, Esc); each kind's distinctive
// action keys come next, then the common extras (Tab, Backspace, Ctrl+O, Ctrl+R).
private val CONTROL_KEY_ORDER = listOf(
    EnterKey,
    UpKey, DownKey, LeftKey, RightKey,
    CtrlCKey,
    ModeKey,
    CtrlLKey,
    EscKey,
    ApproveKey, RejectKey,
    CtrlKKey, CtrlGKey, CtrlDKey,
    RewindKey,
    TabKey, BackspaceKey,
    CtrlOKey, CtrlRKey,
)

// Reorder a set of keys into the canonical layout. Membership is defined by the caller; the
// resulting position is always dictated by CONTROL_KEY_ORDER. Filtering by the (unique) label
// avoids the ByteArray reference-equality pitfall of the ControlKey data class.
private fun orderedControlKeys(keys: List<ControlKey>): List<ControlKey> {
    val present = keys.mapTo(HashSet()) { it.label }
    return CONTROL_KEY_ORDER.filter { it.label in present }
}

// Nav/control keys shared by every CLI. Arrows + Enter drive the in-terminal menus (mode
// picker, approval/selection prompts, history); Ctrl+O toggles the transcript, Ctrl+R
// searches history; Backspace erases characters typed straight into a TUI (terminal-
// surface typing has no composer buffer to delete from). Order here is irrelevant —
// CONTROL_KEY_ORDER decides the on-screen layout; this list only defines membership.
private val SHARED_KEYS = listOf(
    EscKey, CtrlCKey, EnterKey, TabKey, BackspaceKey,
    UpKey, DownKey, LeftKey, RightKey,
    CtrlOKey, CtrlRKey,
)

// Claude / shell / auto-scan: the shared keys plus Mode (Shift+Tab, CSI Z — cycles
// plan/accept-edits/normal), Rewind (Esc-Esc — edits the previous message) and Ctrl+L
// (clear screen).
private val CLAUDE_CONTROL_KEYS =
    orderedControlKeys(listOf(ModeKey, RewindKey) + SHARED_KEYS + CtrlLKey)

// Codex = the Claude set plus its high-frequency approval answers (bare 'y'/'n', no CR —
// Enter stays a separate key).
private val CODEX_CONTROL_KEYS =
    orderedControlKeys(listOf(ApproveKey, RejectKey) + CLAUDE_CONTROL_KEYS)

// Antigravity (agy) adds its own TUI controls — Ctrl+K (fast-approve pending action),
// Ctrl+G (tool confirmation view), Ctrl+D (exit) — to the shared keys and Ctrl+L
// (clear screen). It has no Mode/Rewind. Bracketed paste is honored, so no newline key.
private val ANTIGRAVITY_CONTROL_KEYS =
    orderedControlKeys(listOf(CtrlKKey, CtrlGKey, CtrlDKey) + SHARED_KEYS + CtrlLKey)

// Kind-neutral minimal set for custom agent types and the not-yet-resolved
// state: nav + the universal interrupt/erase keys only. Ctrl+O/Ctrl+R/Ctrl+L
// are deliberately absent — they carry Claude-TUI meanings an arbitrary CLI
// may bind differently; the saved-shortcut "…" picker is the escape hatch.
private val DEFAULT_CONTROL_KEYS = orderedControlKeys(
    listOf(
        EnterKey, UpKey, DownKey, LeftKey, RightKey,
        CtrlCKey, EscKey, TabKey, BackspaceKey,
    ),
)

private fun controlKeysFor(kind: SessionKind?): List<ControlKey> = when (kind) {
    SessionKind.Codex -> CODEX_CONTROL_KEYS
    SessionKind.Antigravity -> ANTIGRAVITY_CONTROL_KEYS
    // null = kind not resolved yet: render the neutral set instead of guessing
    // Claude (its Mode/Rewind escape codes are wrong input for other TUIs). A
    // plain shell is the same case — Mode/Rewind/Ctrl+O/Ctrl+R carry Claude-TUI
    // meanings a shell binds differently or not at all.
    null, is SessionKind.Custom, SessionKind.Shell -> DEFAULT_CONTROL_KEYS
    SessionKind.Claude, SessionKind.AutoScan -> CLAUDE_CONTROL_KEYS
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
    onOpenShortcuts: () -> Unit = {},
    savedShortcuts: List<SavedShortcutUi> = emptyList(),
    onSendSavedShortcut: (SavedShortcutUi) -> Unit = {},
    onRenameSavedShortcut: (SavedShortcutUi) -> Unit = {},
    onRemoveSavedShortcut: (SavedShortcutUi) -> Unit = {},
    onSaveAsTemplate: () -> Unit = {},
    sessionKind: SessionKind? = SessionKind.Claude,
    composerVisible: Boolean = true,
    onRevealComposer: () -> Unit = {},
    onHideComposer: () -> Unit = {},
    onRaiseTerminalKeyboard: () -> Unit = {},
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
        // Composer block (field + action buttons). User-collapsible via the hide
        // button and restored via the reveal chip below; while hidden the terminal
        // surface owns typing.
        //
        // Fade-only transitions (here and on the panel below): a vertical slide animates
        // the InputBar height frame-by-frame, which resizes the terminal view — and
        // thus the remote PTY — through a burst of intermediate row counts. A fade
        // changes height in a single frame, so the whole show/hide coalesces into one
        // PTY resize (with the IME inset jump-cut in TerminalScreen).
        AnimatedVisibility(
            visible = composerVisible,
            enter = fadeIn(),
            exit = fadeOut(),
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
                onHideComposer = onHideComposer,
            )
        }

        // Control-key panel — two rows while the composer is hidden: the keyboard
        // button + reveal chip on top, the horizontally scrollable key row below.
        // Forced visible while the composer is hidden so terminal-surface typing
        // always keeps Esc/arrows/Backspace and the reveal chip reachable.
        AnimatedVisibility(
            visible = panelExpanded || !composerVisible,
            enter = fadeIn(),
            exit = fadeOut(),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 2.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                if (!composerVisible) {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        // With the composer hidden the terminal is the input field: this
                        // re-raises the IME on the terminal surface after the user dismissed it.
                        IconButton(
                            onClick = onRaiseTerminalKeyboard,
                            enabled = enabled,
                            modifier = Modifier.testTag("raise_terminal_keyboard_button"),
                        ) {
                            Icon(
                                Icons.Outlined.Keyboard,
                                contentDescription = "Show keyboard",
                            )
                        }
                        RevealComposerChip(
                            onClick = {
                                panelExpanded = true
                                onRevealComposer()
                            },
                        )
                    }
                }
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    controlKeys.forEach { key ->
                        PixelButton(
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
                    // Trailing chord-picker trigger — every session kind, always
                    // last. Not a ControlKey: it opens the shortcut sheet instead
                    // of sending bytes, so it stays out of CONTROL_KEY_ORDER and
                    // the per-kind membership lists.
                    PixelButton(
                        onClick = {
                            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                            onOpenShortcuts()
                        },
                        enabled = enabled,
                        contentPadding = PaddingValues(
                            horizontal = 10.dp, vertical = 4.dp,
                        ),
                        modifier = Modifier.testTag("custom_shortcut_button"),
                    ) {
                        Icon(
                            Icons.Outlined.MoreHoriz,
                            contentDescription = "Custom shortcut",
                            modifier = Modifier.size(18.dp),
                        )
                    }
                    // User-saved shortcut buttons — the literal end of the row,
                    // after the ⌘ trigger. Rendered for every session kind that
                    // has saved chords; the list is already kind-filtered.
                    // key() pins each button's remembered menu state to its
                    // shortcut id: with positional memoization, a push-driven
                    // removal would hand an open Rename/Remove menu to the
                    // NEXT shortcut in the row.
                    savedShortcuts.forEach { shortcut ->
                        key(shortcut.id) {
                            SavedShortcutKeyButton(
                                shortcut = shortcut,
                                enabled = enabled,
                                onSend = { onSendSavedShortcut(shortcut) },
                                onRename = { onRenameSavedShortcut(shortcut) },
                                onRemove = { onRemoveSavedShortcut(shortcut) },
                            )
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
    onHideComposer: () -> Unit,
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
                onHideComposer = onHideComposer,
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
                    onHideComposer = onHideComposer,
                )
            }
        }
    }
}

/** Labeled affordance that restores a user-hidden composer. */
@Composable
private fun RevealComposerChip(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
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
        modifier = modifier.testTag("reveal_composer_chip"),
    )
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
        // Verbatim input: this text is written straight to the PTY. Capitalization
        // is spelled out rather than left to Compose's default so the intent
        // survives a future default change (iOS parity).
        keyboardOptions = KeyboardOptions(
            capitalization = KeyboardCapitalization.None,
            autoCorrectEnabled = false,
            imeAction = ImeAction.Default,
        ),
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
    onHideComposer: () -> Unit,
) {
    IconButton(
        onClick = onHideComposer,
        modifier = Modifier.testTag("hide_composer_button"),
    ) {
        Icon(
            Icons.Filled.KeyboardHide,
            contentDescription = "Hide command field",
        )
    }
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

/**
 * A user-saved shortcut rendered as a control-row button. Not a
 * [FilledTonalButton]: Material3 buttons have no long-press slot, and
 * long-press must stay reachable even while sends are gated (a disabled
 * node would swallow the gesture), so this is a [Surface] styled to match
 * with [combinedClickable] — tap sends (no-op when `!enabled`, label
 * dimmed), long-press opens the Rename/Remove menu.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun SavedShortcutKeyButton(
    shortcut: SavedShortcutUi,
    enabled: Boolean,
    onSend: () -> Unit,
    onRename: () -> Unit,
    onRemove: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val haptic = LocalHapticFeedback.current
    var menuExpanded by remember { mutableStateOf(false) }
    Box(modifier = modifier) {
        Surface(
            shape = ButtonDefaults.filledTonalShape,
            color = MaterialTheme.colorScheme.secondaryContainer,
            contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
            modifier = Modifier
                .testTag("saved_shortcut_button")
                .semantics { contentDescription = "Saved shortcut ${shortcut.label}" },
        ) {
            Box(
                contentAlignment = Alignment.Center,
                modifier = Modifier
                    .combinedClickable(
                        onClick = {
                            if (enabled) {
                                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                                onSend()
                            }
                        },
                        onLongClick = {
                            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                            menuExpanded = true
                        },
                    )
                    .defaultMinSize(
                        minWidth = ButtonDefaults.MinWidth,
                        minHeight = ButtonDefaults.MinHeight,
                    )
                    .padding(horizontal = 10.dp, vertical = 4.dp),
            ) {
                // The existing "risky" marking (CSI-u chords) carries over to
                // saved buttons as the tertiary label color.
                val labelColor = if (shortcut.risky) {
                    MaterialTheme.colorScheme.tertiary
                } else {
                    MaterialTheme.colorScheme.onSecondaryContainer
                }
                Text(
                    shortcut.label,
                    style = MaterialTheme.typography.labelMedium.copy(
                        fontFamily = FontFamily.Monospace,
                    ),
                    color = labelColor.copy(alpha = if (enabled) 1f else 0.38f),
                )
            }
        }
        DropdownMenu(
            expanded = menuExpanded,
            onDismissRequest = { menuExpanded = false },
        ) {
            DropdownMenuItem(
                text = { Text("Rename") },
                onClick = {
                    menuExpanded = false
                    onRename()
                },
                leadingIcon = { Icon(Icons.Filled.Edit, contentDescription = null) },
            )
            DropdownMenuItem(
                text = { Text("Remove") },
                onClick = {
                    menuExpanded = false
                    onRemove()
                },
                leadingIcon = { Icon(Icons.Filled.Delete, contentDescription = null) },
            )
        }
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
    MobaideTheme {
        InputBar(
            text = TextFieldValue(""),
            onTextChange = {},
            onSend = {},
            onSendBytes = {},
            savedShortcuts = listOf(
                SavedShortcutUi(
                    id = "s1",
                    chord = KeyChord(ctrl = true, key = "x"),
                    name = "Cut",
                    risky = false,
                ),
                SavedShortcutUi(
                    id = "s2",
                    chord = KeyChord(ctrl = true, shift = true, key = "p"),
                    name = null,
                    risky = true,
                ),
            ),
        )
    }
}

@Preview(name = "InputBar composer hidden light", showBackground = true)
@Preview(
    name = "InputBar composer hidden dark",
    showBackground = true,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun InputBarComposerHiddenPreview() {
    MobaideTheme {
        InputBar(
            text = TextFieldValue(""),
            onTextChange = {},
            onSend = {},
            onSendBytes = {},
            composerVisible = false,
        )
    }
}
