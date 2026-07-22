package com.mobaide.app.ui.terminal

import android.content.res.Configuration
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bookmark
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.outlined.BookmarkBorder
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.InputChip
import androidx.compose.material3.InputChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme

/**
 * Chord picker for arbitrary keyboard shortcuts: Ctrl/Alt/Shift toggles plus a
 * base-key grid, with per-session-kind recents on top for one-tap resend and
 * the kind's server-synced saved shortcuts below them (tap = send, ✕ = remove).
 * Tapping a recent, a saved chip, or Send delivers the chord via [onSendChord];
 * the Save/Saved toggle beside the preview pins the composed chord as a button
 * via [onSaveShortcut] or unpins it via [onRemoveSavedShortcut], and naming is
 * a follow-up step routed through [onRenameSavedShortcut]. The sheet stays open
 * so the new chip appears in place; the caller closes it.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ShortcutPickerSheet(
    recents: List<KeyChord>,
    onSendChord: (KeyChord) -> Unit,
    onDismiss: () -> Unit,
    enabled: Boolean = true,
    savedShortcuts: List<SavedShortcutUi> = emptyList(),
    onSaveShortcut: (KeyChord) -> Unit = {},
    onRenameSavedShortcut: (SavedShortcutUi) -> Unit = {},
    onRemoveSavedShortcut: (SavedShortcutUi) -> Unit = {},
    snackbarHostState: SnackbarHostState = remember { SnackbarHostState() },
) {
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        // showSnackbar() suspends until SnackbarData.dismiss()/performAction()
        // runs, and the only auto-dismiss timer is a LaunchedEffect inside the
        // composed SnackbarHost. This sheet leaves composition the instant it is
        // dismissed, so a snackbar still on screen would strand its caller
        // forever — and with it TerminalScreen's single userMessages collector,
        // which then silently drops every later message once the SharedFlow's
        // buffer fills. Hand the pending snackbar an ending on the way out.
        DisposableEffect(snackbarHostState) {
            onDispose { snackbarHostState.currentSnackbarData?.dismiss() }
        }
        // The screen-level SnackbarHost lives in the TerminalScreen Scaffold —
        // behind this sheet's window and scrim — so a failed save or remove
        // triggered here would be invisible. An overlaid host keeps that
        // feedback on the surface the mutation was made from.
        Box(modifier = Modifier.imePadding()) {
            ShortcutPickerContent(
                recents = recents,
                onSendChord = onSendChord,
                onClose = onDismiss,
                enabled = enabled,
                savedShortcuts = savedShortcuts,
                onSaveShortcut = onSaveShortcut,
                onRenameSavedShortcut = onRenameSavedShortcut,
                onRemoveSavedShortcut = onRemoveSavedShortcut,
            )
            SnackbarHost(
                hostState = snackbarHostState,
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(8.dp),
            )
        }
    }
}

// Extracted from the sheet so it can be previewed and click-tested directly:
// Robolectric's input dispatcher cannot deliver clicks into the ModalBottomSheet
// popup window (see TemplatesPickerSheetTest).
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ShortcutPickerContent(
    recents: List<KeyChord>,
    onSendChord: (KeyChord) -> Unit,
    onClose: () -> Unit,
    // Same session gate as the control-key row and FullScreenInputSheet: while
    // the session is not running, chords can't reach the PTY, so the send
    // surfaces (recents chips + Send) disable rather than pretend to send and
    // record a recent for a dropped keystroke. Composing stays available —
    // and so do the save toggle and chip-remove, which are server mutations,
    // not sends.
    enabled: Boolean = true,
    savedShortcuts: List<SavedShortcutUi> = emptyList(),
    onSaveShortcut: (KeyChord) -> Unit = {},
    onRenameSavedShortcut: (SavedShortcutUi) -> Unit = {},
    onRemoveSavedShortcut: (SavedShortcutUi) -> Unit = {},
    modifier: Modifier = Modifier,
) {
    var ctrl by remember { mutableStateOf(false) }
    var alt by remember { mutableStateOf(false) }
    var shift by remember { mutableStateOf(false) }
    var selectedKey by remember { mutableStateOf<String?>(null) }

    val chord = selectedKey?.let { KeyChord(ctrl = ctrl, alt = alt, shift = shift, key = it) }
    val encoding = chord?.let { KeyChordEncoder.encode(it) }
    val selectedModifiers = listOf(ctrl, alt, shift).count { it }
    // The composed chord's already-pinned row, if any. KeyChord is a data class,
    // so this is exact structural equality — no token round-trip needed.
    val savedMatch = remember(savedShortcuts, chord) {
        chord?.let { c -> savedShortcuts.firstOrNull { it.chord == c } }
    }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "Keyboard shortcut",
                style = MaterialTheme.typography.titleMedium,
            )
            IconButton(onClick = onClose) {
                Icon(Icons.Default.Close, contentDescription = "Close")
            }
        }
        Column(
            // Weighted with fill=false so the footer (preview + Send) is measured
            // first and this grid only gets the leftover height: an unweighted
            // scroll child would consume the whole sheet on short screens
            // (landscape) and starve Send off-screen. fill=false keeps the sheet
            // wrap-content when the grid is shorter than the available space.
            modifier = Modifier
                .weight(1f, fill = false)
                .heightIn(max = 420.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (recents.isNotEmpty()) {
                SectionHeader("Recents")
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    recents.forEach { recent ->
                        SuggestionChip(
                            onClick = { onSendChord(recent) },
                            enabled = enabled,
                            label = { ChipLabel(recent.display()) },
                        )
                    }
                }
            }
            if (savedShortcuts.isNotEmpty()) {
                SectionHeader("Saved")
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    savedShortcuts.forEach { saved ->
                        SavedShortcutChip(
                            shortcut = saved,
                            enabled = enabled,
                            onSend = { onSendChord(saved.chord) },
                            onRemove = { onRemoveSavedShortcut(saved) },
                        )
                    }
                }
            }
            SectionHeader("Modifiers")
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                // Max chord = 2 modifiers + 1 base key: the third, unselected
                // modifier disables once two are on.
                ModifierChip("Ctrl", ctrl, ctrl || selectedModifiers < 2, "shortcut_mod_ctrl") {
                    ctrl = !ctrl
                }
                ModifierChip("Alt", alt, alt || selectedModifiers < 2, "shortcut_mod_alt") {
                    alt = !alt
                }
                ModifierChip("Shift", shift, shift || selectedModifiers < 2, "shortcut_mod_shift") {
                    shift = !shift
                }
            }
            SectionHeader("Letters")
            KeyGrid(KeyChordKeys.letters, selectedKey) { selectedKey = it }
            SectionHeader("Numbers")
            KeyGrid(KeyChordKeys.digits, selectedKey) { selectedKey = it }
            SectionHeader("Function & special")
            KeyGrid(KeyChordKeys.functionKeys + KeyChordKeys.specials, selectedKey) {
                selectedKey = it
            }
        }
        HorizontalDivider()
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(
                modifier = Modifier
                    .weight(1f)
                    .testTag("shortcut_preview"),
            ) {
                if (chord != null && encoding != null) {
                    Text(
                        text = chord.display(),
                        style = MaterialTheme.typography.titleSmall.copy(
                            fontFamily = FontFamily.Monospace,
                        ),
                    )
                    Text(
                        text = buildString {
                            append("Sends: ${encoding.summary}")
                            encoding.sameAsPlainKey?.let { append(" — same byte as $it") }
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    Text(
                        text = "Pick a key",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            SaveToggleButton(
                saved = savedMatch != null,
                enabled = chord != null,
                onClick = {
                    val pinned = savedMatch
                    if (pinned != null) onRemoveSavedShortcut(pinned)
                    else chord?.let(onSaveShortcut)
                },
            )
        }
        if (encoding?.usesCsiU == true) {
            Text(
                text = "CSI-u sequence — may not work in every CLI",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.tertiary,
                modifier = Modifier.testTag("shortcut_risky_hint"),
            )
        }
        savedMatch?.let { pinned ->
            // Doubles as the save confirmation and the already-pinned marker:
            // it shows for any composed chord that is already a button, not
            // only right after a save.
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("shortcut_saved_hint"),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    Icons.Default.Check,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(16.dp),
                )
                Text(
                    text = "Saved as \"${pinned.label}\"",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                TextButton(
                    onClick = { onRenameSavedShortcut(pinned) },
                    modifier = Modifier.testTag("shortcut_rename_button"),
                ) {
                    Text(if (pinned.name == null) "Name" else "Rename")
                }
            }
        }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(bottom = 16.dp),
        ) {
            Button(
                onClick = { chord?.let(onSendChord) },
                enabled = enabled && chord != null,
                modifier = Modifier
                    .weight(1f)
                    .testTag("shortcut_send_button"),
            ) {
                Text("Send")
            }
        }
    }
}

/**
 * Two-state pin control for the composed chord: "Save" persists it as a button,
 * "Saved" unpins it again. Deliberately NOT gated on the session-active flag —
 * both directions are server mutations, not PTY sends.
 *
 * No in-flight state on purpose: the derived saved-state is the only signal that
 * a mutation landed, so a pending flag would need a timeout of its own to
 * recover from a request that never completes — more machinery than this earns.
 * The cost is that the button stays tappable for the whole round trip, so
 * making a double tap harmless is the ViewModel's job, not this button's —
 * neither server operation is idempotent on its own. [TerminalViewModel] drops
 * a concurrent duplicate save (a late create landing after a delete would
 * otherwise resurrect the removed chord) and absorbs a repeat delete (DELETE
 * answers 403 once the row is gone).
 */
@Composable
private fun SaveToggleButton(
    saved: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    val modifier = Modifier.testTag("shortcut_save_button")
    if (saved) {
        FilledTonalButton(onClick = onClick, enabled = enabled, modifier = modifier) {
            SaveToggleLabel(Icons.Default.Bookmark, "Saved")
        }
    } else {
        OutlinedButton(onClick = onClick, enabled = enabled, modifier = modifier) {
            SaveToggleLabel(Icons.Outlined.BookmarkBorder, "Save")
        }
    }
}

@Composable
private fun SaveToggleLabel(icon: ImageVector, label: String) {
    Icon(
        icon,
        contentDescription = null,
        modifier = Modifier.size(ButtonDefaults.IconSize),
    )
    Spacer(Modifier.size(ButtonDefaults.IconSpacing))
    Text(label)
}

/**
 * A saved shortcut as a picker chip: tap resends the chord (no-op while the
 * session gate is down — the label dims), the trailing ✕ removes it on the
 * server (never gated: removal must work even when the session is stopped).
 */
@Composable
private fun SavedShortcutChip(
    shortcut: SavedShortcutUi,
    enabled: Boolean,
    onSend: () -> Unit,
    onRemove: () -> Unit,
) {
    val labelBase = if (shortcut.risky) {
        MaterialTheme.colorScheme.tertiary
    } else {
        MaterialTheme.colorScheme.onSurfaceVariant
    }
    InputChip(
        selected = false,
        onClick = { if (enabled) onSend() },
        label = {
            Text(
                text = shortcut.label,
                style = MaterialTheme.typography.labelMedium.copy(
                    fontFamily = FontFamily.Monospace,
                ),
                color = labelBase.copy(alpha = if (enabled) 1f else 0.38f),
            )
        },
        trailingIcon = {
            // The ✕ needs real 48dp layout bounds: a bare 18dp clickable icon
            // gets the 48dp minimum touch target as an invisible expansion
            // that overlaps the chip label, so taps meant to SEND would
            // silently REMOVE instead (destructive, synced, no undo).
            Box(
                contentAlignment = Alignment.Center,
                modifier = Modifier
                    .minimumInteractiveComponentSize()
                    .clickable { onRemove() },
            ) {
                Icon(
                    Icons.Default.Close,
                    contentDescription = "Remove ${shortcut.label}",
                    modifier = Modifier.size(InputChipDefaults.IconSize),
                )
            }
        },
        modifier = Modifier.testTag("saved_shortcut_chip"),
    )
}

@Composable
private fun SectionHeader(label: String) {
    Text(
        text = label,
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun ChipLabel(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelMedium.copy(
            fontFamily = FontFamily.Monospace,
        ),
    )
}

@Composable
private fun ModifierChip(
    label: String,
    selected: Boolean,
    enabled: Boolean,
    tag: String,
    onToggle: () -> Unit,
) {
    FilterChip(
        selected = selected,
        onClick = onToggle,
        enabled = enabled,
        label = { ChipLabel(label) },
        elevation = FilterChipDefaults.filterChipElevation(),
        modifier = Modifier.testTag(tag),
    )
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun KeyGrid(
    keys: List<String>,
    selectedKey: String?,
    onSelect: (String?) -> Unit,
) {
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        keys.forEach { key ->
            FilterChip(
                selected = key == selectedKey,
                onClick = { onSelect(if (key == selectedKey) null else key) },
                label = { ChipLabel(KeyChordKeys.displayName(key)) },
                elevation = FilterChipDefaults.filterChipElevation(),
                modifier = Modifier.defaultMinSize(minWidth = 48.dp),
            )
        }
    }
}

private val previewRecents = listOf(
    KeyChord(ctrl = true, key = "r"),
    KeyChord(ctrl = true, shift = true, key = "i"),
    KeyChord(alt = true, key = "f4"),
)

private val previewSavedShortcuts = listOf(
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
)

@Preview(name = "ShortcutPicker light", showBackground = true)
@Preview(
    name = "ShortcutPicker dark",
    showBackground = true,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun ShortcutPickerContentPreview() {
    MobaideTheme(dynamicColor = false) {
        Surface {
            ShortcutPickerContent(
                recents = previewRecents,
                onSendChord = {},
                onClose = {},
                savedShortcuts = previewSavedShortcuts,
            )
        }
    }
}

// The Save/Saved states are not reachable from the sheet previews below: the
// composed chord lives in ShortcutPickerContent's own selectedKey state, so a
// preview cannot pick a key. Preview the toggle directly instead.
@Preview(name = "Save toggle light", showBackground = true)
@Preview(
    name = "Save toggle dark",
    showBackground = true,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun SaveToggleButtonPreview() {
    MobaideTheme(dynamicColor = false) {
        Surface {
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.padding(16.dp),
            ) {
                SaveToggleButton(saved = false, enabled = false, onClick = {})
                SaveToggleButton(saved = false, enabled = true, onClick = {})
                SaveToggleButton(saved = true, enabled = true, onClick = {})
            }
        }
    }
}

@Preview(name = "ShortcutPicker empty recents", showBackground = true)
@Composable
private fun ShortcutPickerContentEmptyRecentsPreview() {
    MobaideTheme(dynamicColor = false) {
        Surface {
            ShortcutPickerContent(
                recents = emptyList(),
                onSendChord = {},
                onClose = {},
            )
        }
    }
}
