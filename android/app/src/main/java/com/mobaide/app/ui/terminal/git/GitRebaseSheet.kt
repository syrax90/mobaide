package com.mobaide.app.ui.terminal.git

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme

const val GIT_REBASE_SHEET_TAG = "git_rebase_sheet"
const val GIT_REBASE_ONTO_PICKER_TAG = "git_rebase_onto_picker"
const val GIT_REBASE_ONTO_FIELD_TAG = "git_rebase_onto_field"
const val GIT_REBASE_BLOCKER_TAG = "git_rebase_blocker"
const val GIT_REBASE_ERROR_TAG = "git_rebase_error"
const val GIT_REBASE_SUBMIT_TAG = "git_rebase_submit"

/**
 * The Rebase sheet: pick the branch to replay this one on top of. A
 * project-backed session picks from the project's local and remote-tracking
 * branches, deliberately with no preselection for a history rewrite; any
 * other session — or a list that failed to load — types the ref. What would
 * make git refuse to start is explained up front and keeps the button
 * disabled; the agent still decides.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun GitRebaseSheet(
    state: RebaseSheetState,
    status: GitStatusResponse?,
    onDismiss: () -> Unit,
    onRebase: (onto: String) -> Unit,
) {
    PixelBottomSheet(
        onDismissRequest = { if (!state.starting) onDismiss() },
        sheetState = rememberModalBottomSheetState(
            skipPartiallyExpanded = true,
            confirmValueChange = { !state.starting },
        ),
        modifier = Modifier.testTag(GIT_REBASE_SHEET_TAG),
    ) {
        GitRebaseForm(state = state, status = status, onDismiss = onDismiss, onRebase = onRebase)
    }
}

/** `internal` so a test can compose the form without a sheet window. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun GitRebaseForm(
    state: RebaseSheetState,
    status: GitStatusResponse?,
    onDismiss: () -> Unit,
    onRebase: (onto: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val repo = status?.repo
    val targets = state.targets
    val options = remember(targets, repo?.head?.branch) {
        (targets as? RebaseTargetsState.Ready)?.items?.let { rebaseTargets(it, repo?.head?.branch) }
    }
    var picked by rememberSaveable { mutableStateOf<String?>(null) }
    var typed by rememberSaveable { mutableStateOf("") }
    var pickerOpen by remember { mutableStateOf(false) }
    val busy = state.starting
    val blocker = rebaseBlocker(status)
    val onto = when (targets) {
        is RebaseTargetsState.Ready -> picked
        RebaseTargetsState.Typed -> typed.trim().takeIf { it.isNotEmpty() }
        RebaseTargetsState.Loading -> null
    }
    val submittable = !busy && blocker == null && onto != null

    Column(
        modifier = modifier
            .fillMaxWidth()
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp)
            .padding(bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = repo?.let { "Rebase ${headLabel(it)}" } ?: "Rebase",
            style = MaterialTheme.typography.titleMedium.copy(fontFamily = ChromeFontFamily),
            color = MaterialTheme.colorScheme.primary,
        )
        Text(
            text = "Replays this branch's commits on top of the branch you pick.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        when (targets) {
            RebaseTargetsState.Loading -> OutlinedTextField(
                colors = PixelTextFieldDefaults.colors(),
                value = "Loading branches…",
                onValueChange = {},
                readOnly = true,
                enabled = false,
                label = { Text("Onto") },
                modifier = Modifier.fillMaxWidth(),
            )
            is RebaseTargetsState.Ready -> {
                val choices = options.orEmpty()
                ExposedDropdownMenuBox(
                    expanded = pickerOpen,
                    onExpandedChange = { if (!busy && choices.isNotEmpty()) pickerOpen = !pickerOpen },
                ) {
                    OutlinedTextField(
                        colors = PixelTextFieldDefaults.colors(),
                        value = choices.firstOrNull { it.value == picked }?.label
                            ?: if (choices.isEmpty()) "No other branches" else "Choose a branch",
                        onValueChange = { /* read-only — selection-only field */ },
                        readOnly = true,
                        enabled = !busy,
                        label = { Text("Onto") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = pickerOpen) },
                        modifier = Modifier
                            .fillMaxWidth()
                            .menuAnchor()
                            .testTag(GIT_REBASE_ONTO_PICKER_TAG),
                    )
                    ExposedDropdownMenu(
                        expanded = pickerOpen,
                        onDismissRequest = { pickerOpen = false },
                    ) {
                        choices.forEach { option ->
                            DropdownMenuItem(
                                text = { Text(option.label) },
                                onClick = {
                                    picked = option.value
                                    pickerOpen = false
                                },
                            )
                        }
                    }
                }
            }
            RebaseTargetsState.Typed -> OutlinedTextField(
                colors = PixelTextFieldDefaults.colors(),
                value = typed,
                onValueChange = { typed = it },
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag(GIT_REBASE_ONTO_FIELD_TAG),
                label = { Text("Onto") },
                placeholder = { Text("Branch, tag or commit") },
                singleLine = true,
                enabled = !busy,
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.None,
                    autoCorrectEnabled = false,
                ),
            )
        }
        blocker?.let { reason ->
            Text(
                text = reason,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.testTag(GIT_REBASE_BLOCKER_TAG),
            )
        }
        state.error?.let { error ->
            Column(
                modifier = Modifier.testTag(GIT_REBASE_ERROR_TAG),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    text = error.message,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
                error.output?.let { output ->
                    Text(
                        text = output,
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp, Alignment.End),
        ) {
            PixelButton(text = "Cancel", onClick = onDismiss, enabled = !busy)
            PixelButton(
                text = if (busy) "Rebasing…" else "Rebase",
                onClick = { onto?.let(onRebase) },
                style = PixelButtonStyle.Primary,
                enabled = submittable,
                modifier = Modifier.testTag(GIT_REBASE_SUBMIT_TAG),
            )
        }
    }
}

@Composable
private fun GitRebasePreviewContent() {
    val base = previewGitStatus()
    val status = base.copy(
        repo = base.repo?.copy(state = GitRepoInfo.STATE_CLEAN),
        entries = emptyList(),
        counts = null,
    )
    GitRebaseForm(
        state = RebaseSheetState(
            targets = RebaseTargetsState.Ready(
                listOf(
                    GitItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "main"),
                    GitItem(kind = GitItem.KIND_REMOTE_BRANCH, branch = "origin/main", remote = "origin"),
                ),
            ),
        ),
        status = status,
        onDismiss = {},
        onRebase = {},
    )
}

@Preview(name = "Rebase sheet dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun GitRebasePreviewDark() {
    MobaideTheme(darkTheme = true) { GitRebasePreviewContent() }
}

@Preview(name = "Rebase sheet light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun GitRebasePreviewLight() {
    MobaideTheme(darkTheme = false) { GitRebasePreviewContent() }
}
