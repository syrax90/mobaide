package com.mobaide.app.ui.herdr

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.HerdrAgentKind
import com.mobaide.app.data.network.dto.HerdrTarget
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import com.mobaide.app.ui.theme.MobaideTheme

private const val LABEL_MAX_CHARS = 80

const val HERDR_CREATE_CONFIRM_TAG = "herdr_create_confirm"
const val HERDR_CLOSE_CONFIRM_TAG = "herdr_close_confirm"
const val HERDR_DIALOG_ERROR_TAG = "herdr_dialog_error"

/** The reason an action failed, inside its dialog. */
@Composable
private fun DialogError(error: String?) {
    if (error == null) return
    Text(
        text = error,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.error,
        modifier = Modifier.testTag(HERDR_DIALOG_ERROR_TAG),
    )
}

/** What "New workspace" and "New tab" collect. */
data class HerdrCreateValues(val cwd: String?, val label: String?, val agent: String?)

/**
 * A labelled choice field: the read-only select that the other forms use
 * (`GitNewBranchSheet`'s start point).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun <T> ChoiceField(
    label: String,
    selected: String,
    options: List<Pair<String, T>>,
    onSelect: (T) -> Unit,
    enabled: Boolean,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { if (enabled) expanded = !expanded },
        modifier = modifier,
    ) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = selected,
            onValueChange = { /* read-only: selection-only field */ },
            readOnly = true,
            enabled = enabled,
            label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier
                .fillMaxWidth()
                .menuAnchor(),
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { (text, value) ->
                DropdownMenuItem(
                    text = { Text(text) },
                    onClick = {
                        expanded = false
                        onSelect(value)
                    },
                )
            }
        }
    }
}

/**
 * New workspace or new tab. The folder comes from the agent's projects, with a
 * free path as the last choice, so the usual case is two taps.
 *
 * @param forWorkspace a workspace needs a folder; a tab opens in its workspace's folder.
 * @param noun what the server calls a top-level group ("workspace", or "terminal" on herdr-core).
 */
@Composable
fun HerdrCreateDialog(
    forWorkspace: Boolean,
    noun: String,
    projects: List<ProjectInfo>,
    agents: List<HerdrAgentKind>,
    busy: Boolean,
    /** Why the last try failed; shown here, where the user looks. */
    error: String? = null,
    onCreate: (HerdrCreateValues) -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    // null = the default folder, "" = a path typed below, else a project path.
    var folder by remember { mutableStateOf<String?>(null) }
    var customPath by remember { mutableStateOf("") }
    var label by remember { mutableStateOf("") }
    var agent by remember { mutableStateOf<String?>(null) }

    val custom = folder == ""
    val path = customPath.trim()
    val pathInvalid = custom && path.isNotEmpty() &&
        !(path.startsWith("/") || path.startsWith("~") || Regex("^[A-Za-z]:[\\\\/].*").matches(path))
    val canCreate = !busy && !pathInvalid && !(custom && path.isEmpty())

    PixelAlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        modifier = modifier,
        title = { Text(if (forWorkspace) "New $noun" else "New tab") },
        text = {
            // The reason of a failure stays under the fields, outside their
            // scroll: in landscape the fields fill the dialog.
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Column(
                    modifier = Modifier
                        .weight(1f, fill = false)
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    if (forWorkspace) {
                        val folderOptions = buildList<Pair<String, String?>> {
                            add("Default folder" to null)
                            projects.forEach { add(it.name to it.path) }
                            add("Another folder…" to "")
                        }
                        ChoiceField(
                            label = "Folder",
                            selected = folderOptions.firstOrNull { it.second == folder }?.first ?: "Default folder",
                            options = folderOptions,
                            onSelect = { folder = it },
                            enabled = !busy,
                        )
                        if (custom) {
                            OutlinedTextField(
                                colors = PixelTextFieldDefaults.colors(),
                                value = customPath,
                                onValueChange = { customPath = it },
                                modifier = Modifier.fillMaxWidth(),
                                label = { Text("Folder path") },
                                placeholder = { Text("/home/me/project") },
                                isError = pathInvalid,
                                supportingText = {
                                    if (pathInvalid) Text("Enter a full path, for example /home/me/project.")
                                },
                                singleLine = true,
                            )
                        }
                    }
                    OutlinedTextField(
                        colors = PixelTextFieldDefaults.colors(),
                        value = label,
                        onValueChange = { label = it.take(LABEL_MAX_CHARS) },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("Name (optional)") },
                        singleLine = true,
                    )
                    if (agents.isNotEmpty()) {
                        val agentOptions = buildList<Pair<String, String?>> {
                            add("None (a terminal)" to null)
                            agents.forEach { add(it.label to it.id) }
                        }
                        ChoiceField(
                            label = "Start an agent",
                            selected = agentOptions.firstOrNull { it.second == agent }?.first ?: "None (a terminal)",
                            options = agentOptions,
                            onSelect = { agent = it },
                            enabled = !busy,
                        )
                    }
                }
                DialogError(error)
            }
        },
        confirmButton = {
            PixelTextButton(
                onClick = {
                    onCreate(
                        HerdrCreateValues(
                            cwd = if (!forWorkspace) null else if (custom) path else folder,
                            label = label.trim().ifEmpty { null },
                            agent = agent,
                        ),
                    )
                },
                enabled = canCreate,
                modifier = Modifier.testTag(HERDR_CREATE_CONFIRM_TAG),
            ) { Text(if (busy) "Creating…" else "Create") }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss, enabled = !busy) { Text("Cancel") }
        },
    )
}

@Composable
fun HerdrRenameDialog(
    target: String,
    current: String,
    busy: Boolean,
    /** Why the last try failed; shown here, where the user looks. */
    error: String? = null,
    onRename: (String) -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var label by remember { mutableStateOf(current) }
    val trimmed = label.trim()
    PixelAlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        modifier = modifier,
        title = { Text("Rename ${targetNoun(target)}") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    colors = PixelTextFieldDefaults.colors(),
                    value = label,
                    onValueChange = { label = it.take(LABEL_MAX_CHARS) },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("Name") },
                    singleLine = true,
                )
                DialogError(error)
            }
        },
        confirmButton = {
            PixelTextButton(
                onClick = { onRename(trimmed) },
                enabled = !busy && trimmed.isNotEmpty() && trimmed != current,
            ) { Text(if (busy) "Renaming…" else "Rename") }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss, enabled = !busy) { Text("Cancel") }
        },
    )
}

/**
 * Herdr closes at once and cannot undo it, so the question is asked here, and
 * it says what is lost: the programs in the panes stop.
 */
@Composable
fun HerdrCloseDialog(
    target: String,
    label: String,
    busy: Boolean,
    /** Why the last try failed; shown here, where the user looks. */
    error: String? = null,
    onClose: () -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val noun = targetNoun(target)
    PixelAlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        modifier = modifier,
        title = { Text("Close $noun?") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    "Close \"$label\"? " +
                        if (target == HerdrTarget.PANE) "The program in this pane stops."
                        else "Every pane in this $noun closes and its programs stop.",
                )
                DialogError(error)
            }
        },
        confirmButton = {
            PixelTextButton(
                onClick = onClose,
                enabled = !busy,
                modifier = Modifier.testTag(HERDR_CLOSE_CONFIRM_TAG),
            ) {
                Text(if (busy) "Closing…" else "Close", color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss, enabled = !busy) { Text("Cancel") }
        },
    )
}

@Composable
fun HerdrStartAgentDialog(
    agents: List<HerdrAgentKind>,
    busy: Boolean,
    /** Why the last try failed; shown here, where the user looks. */
    error: String? = null,
    onStart: (String) -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    PixelAlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        modifier = modifier,
        title = { Text("Start an agent") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Column(
                    // Seven agents do not fit a landscape dialog.
                    modifier = Modifier
                        .weight(1f, fill = false)
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text("The agent starts in this pane's terminal.")
                    agents.forEach { kind ->
                        PixelButton(
                            text = kind.label,
                            onClick = { onStart(kind.id) },
                            enabled = !busy,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
                DialogError(error)
            }
        },
        confirmButton = {},
        dismissButton = {
            PixelTextButton(onClick = onDismiss, enabled = !busy) { Text("Cancel") }
        },
    )
}

private fun targetNoun(target: String): String = when (target) {
    HerdrTarget.WORKSPACE -> "workspace"
    HerdrTarget.TAB -> "tab"
    else -> "pane"
}

@Composable
private fun HerdrCreateDialogPreviewContent() {
    HerdrCreateDialog(
        forWorkspace = true,
        noun = "workspace",
        projects = emptyList(),
        agents = listOf(HerdrAgentKind("claude", "Claude Code")),
        busy = false,
        onCreate = {},
        onDismiss = {},
    )
}

@Preview(name = "Herdr create dialog dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun HerdrCreateDialogPreviewDark() {
    MobaideTheme(darkTheme = true) { HerdrCreateDialogPreviewContent() }
}

@Preview(name = "Herdr create dialog light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun HerdrCreateDialogPreviewLight() {
    MobaideTheme(darkTheme = false) { HerdrCreateDialogPreviewContent() }
}

@Composable
private fun HerdrRenameDialogPreviewContent() {
    HerdrRenameDialog(target = HerdrTarget.TAB, current = "main", busy = false, onRename = {}, onDismiss = {})
}

@Preview(name = "Herdr rename dialog dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun HerdrRenameDialogPreviewDark() {
    MobaideTheme(darkTheme = true) { HerdrRenameDialogPreviewContent() }
}

@Preview(name = "Herdr rename dialog light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun HerdrRenameDialogPreviewLight() {
    MobaideTheme(darkTheme = false) { HerdrRenameDialogPreviewContent() }
}

@Composable
private fun HerdrCloseDialogPreviewContent() {
    HerdrCloseDialog(target = HerdrTarget.PANE, label = "api refactor", busy = false, onClose = {}, onDismiss = {})
}

@Preview(name = "Herdr close dialog dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun HerdrCloseDialogPreviewDark() {
    MobaideTheme(darkTheme = true) { HerdrCloseDialogPreviewContent() }
}

@Preview(name = "Herdr close dialog light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun HerdrCloseDialogPreviewLight() {
    MobaideTheme(darkTheme = false) { HerdrCloseDialogPreviewContent() }
}

@Composable
private fun HerdrStartAgentDialogPreviewContent() {
    HerdrStartAgentDialog(
        agents = listOf(HerdrAgentKind("claude", "Claude Code"), HerdrAgentKind("codex", "Codex")),
        busy = false,
        onStart = {},
        onDismiss = {},
    )
}

@Preview(name = "Herdr start agent dialog dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun HerdrStartAgentDialogPreviewDark() {
    MobaideTheme(darkTheme = true) { HerdrStartAgentDialogPreviewContent() }
}

@Preview(name = "Herdr start agent dialog light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun HerdrStartAgentDialogPreviewLight() {
    MobaideTheme(darkTheme = false) { HerdrStartAgentDialogPreviewContent() }
}
