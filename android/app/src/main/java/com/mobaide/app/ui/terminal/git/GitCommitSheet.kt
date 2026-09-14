package com.mobaide.app.ui.terminal.git

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.GitCommitAuthor
import com.mobaide.app.data.network.dto.GitCommitHead
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelCheckbox
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

const val GIT_COMMIT_SHEET_TAG = "git_commit_sheet"
const val GIT_COMMIT_SUMMARY_TAG = "git_commit_summary"
const val GIT_COMMIT_DESCRIPTION_TAG = "git_commit_description"
const val GIT_COMMIT_AMEND_TAG = "git_commit_amend"
const val GIT_COMMIT_NO_VERIFY_TAG = "git_commit_no_verify"
const val GIT_COMMIT_SUBMIT_TAG = "git_commit_submit"
const val GIT_COMMIT_ERROR_TAG = "git_commit_error"

/**
 * The commit sheet: Summary + Description (sent as `subject\n\nbody`), the
 * author line from `commit/context`, Amend with the previous message
 * prefilled, and Skip hooks. Field text is local, as in `SaveTemplateDialog`;
 * the ViewModel only sees the submit.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun GitCommitSheet(
    state: CommitSheetState,
    stagedCount: Int,
    onDismiss: () -> Unit,
    onCommit: (summary: String, description: String, amend: Boolean, noVerify: Boolean) -> Unit,
) {
    val focusRequester = remember { FocusRequester() }
    CommitSheetShell(onDismiss = onDismiss, busy = state.committing) {
        GitCommitForm(
            state = state,
            stagedCount = stagedCount,
            focusRequester = focusRequester,
            onDismiss = onDismiss,
            onCommit = onCommit,
        )
    }
    LaunchedEffect(Unit) { focusRequester.requestFocus() }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CommitSheetShell(
    onDismiss: () -> Unit,
    busy: Boolean,
    content: @Composable () -> Unit,
) {
    PixelBottomSheet(
        onDismissRequest = { if (!busy) onDismiss() },
        sheetState = rememberModalBottomSheetState(
            skipPartiallyExpanded = true,
            confirmValueChange = { !busy },
        ),
        modifier = Modifier.testTag(GIT_COMMIT_SHEET_TAG),
    ) {
        content()
    }
}

/** `internal` so a test can compose the form without a sheet window. */
@Composable
internal fun GitCommitForm(
    state: CommitSheetState,
    stagedCount: Int,
    onDismiss: () -> Unit,
    onCommit: (summary: String, description: String, amend: Boolean, noVerify: Boolean) -> Unit,
    modifier: Modifier = Modifier,
    focusRequester: FocusRequester = remember { FocusRequester() },
) {
    var summary by rememberSaveable { mutableStateOf("") }
    var description by rememberSaveable { mutableStateOf("") }
    var amend by rememberSaveable { mutableStateOf(false) }
    var noVerify by rememberSaveable { mutableStateOf(false) }
    // What Amend poured into the fields, so switching it off can take back
    // exactly that and nothing the user typed since.
    var prefill by remember { mutableStateOf<Pair<String, String>?>(null) }

    val context = state.context
    val ready = context as? CommitContextState.Ready
    val author = ready?.author
    val head = ready?.head
    val busy = state.committing
    val submittable = !busy && author != null && summary.isNotBlank() && (stagedCount > 0 || amend)

    fun toggleAmend(checked: Boolean) {
        amend = checked
        if (head == null) return
        if (checked) {
            if (summary.isBlank() && description.isBlank()) {
                prefill = head.subject to head.body
                summary = head.subject
                description = head.body
            }
            return
        }
        val poured = prefill
        prefill = null
        if (poured != null && summary == poured.first && description == poured.second) {
            summary = ""
            description = ""
        }
    }

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
            text = if (amend) "Amend commit" else "Commit",
            style = MaterialTheme.typography.titleMedium.copy(fontFamily = ChromeFontFamily),
            color = MaterialTheme.colorScheme.primary,
        )
        when (context) {
            CommitContextState.Loading -> PixelBlinkCursor()
            is CommitContextState.Failed -> Text(
                text = context.message,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
            is CommitContextState.Ready -> {
                if (author != null) {
                    Text(
                        text = authorLine(author),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    IdentityPanel()
                }
            }
        }
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = summary,
            onValueChange = { summary = it },
            modifier = Modifier
                .fillMaxWidth()
                .focusRequester(focusRequester)
                .testTag(GIT_COMMIT_SUMMARY_TAG),
            label = { Text("Summary") },
            placeholder = { Text("What changed") },
            singleLine = true,
            enabled = !busy,
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.Sentences,
                autoCorrectEnabled = true,
            ),
        )
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = description,
            onValueChange = { description = it },
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 96.dp)
                .testTag(GIT_COMMIT_DESCRIPTION_TAG),
            label = { Text("Description") },
            placeholder = { Text("Optional details") },
            enabled = !busy,
            minLines = 3,
            textStyle = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
        )
        if (head != null) {
            CheckRow(
                label = "Amend previous commit",
                hint = "Rewrites ${head.short_oid} · ${head.subject}",
                checked = amend,
                enabled = !busy,
                onCheckedChange = ::toggleAmend,
                tag = GIT_COMMIT_AMEND_TAG,
            )
        }
        CheckRow(
            label = "Skip hooks (--no-verify)",
            hint = null,
            checked = noVerify,
            enabled = !busy,
            onCheckedChange = { noVerify = it },
            tag = GIT_COMMIT_NO_VERIFY_TAG,
        )
        state.error?.let { error ->
            Column(
                modifier = Modifier.testTag(GIT_COMMIT_ERROR_TAG),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    text = error.message,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
                error.output?.takeIf { it.isNotBlank() }?.let { output ->
                    Text(
                        text = output,
                        style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
                            .padding(8.dp)
                            .heightIn(max = 160.dp),
                    )
                }
            }
        }
        if (stagedCount == 0 && !amend && ready != null) {
            Text(
                text = "Nothing is staged. Stage files first, or amend the previous commit.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp, Alignment.End),
        ) {
            PixelButton(text = "Cancel", onClick = onDismiss, enabled = !busy)
            PixelButton(
                text = if (busy) "Committing…" else commitButtonLabel(stagedCount, amend),
                onClick = { onCommit(summary, description, amend, noVerify) },
                style = PixelButtonStyle.Primary,
                enabled = submittable,
                modifier = Modifier.testTag(GIT_COMMIT_SUBMIT_TAG),
            )
        }
    }
}

@Composable
private fun CheckRow(
    label: String,
    hint: String?,
    checked: Boolean,
    enabled: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    tag: String,
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        PixelCheckbox(
            checked = checked,
            onCheckedChange = onCheckedChange,
            enabled = enabled,
            modifier = Modifier.testTag(tag),
        )
        Spacer(Modifier.width(4.dp))
        Column {
            Text(label, style = MaterialTheme.typography.bodyMedium)
            if (hint != null) {
                Text(
                    text = hint,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun IdentityPanel() {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(
            text = "Git doesn't know who you are on the agent machine. Run these there, then try again:",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
        )
        Text(
            text = IDENTITY_FIX_COMMANDS.joinToString("\n"),
            style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
            color = MaterialTheme.pixel.warning,
            modifier = Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.surfaceContainerHigh)
                .padding(8.dp),
        )
    }
}

@Composable
private fun GitCommitFormPreviewContent() {
    GitCommitForm(
        state = CommitSheetState(
            context = CommitContextState.Ready(
                author = GitCommitAuthor("Ada Lovelace", "ada@example.com"),
                head = GitCommitHead("4890423520f4dcd158b85374f44fc8819a813ac2", "4890423", "Fix the login redirect"),
            ),
        ),
        stagedCount = 3,
        onDismiss = {},
        onCommit = { _, _, _, _ -> },
    )
}

@Preview(name = "Commit sheet dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun GitCommitFormPreviewDark() {
    MobaideTheme(darkTheme = true) { GitCommitFormPreviewContent() }
}

@Preview(name = "Commit sheet light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun GitCommitFormPreviewLight() {
    MobaideTheme(darkTheme = false) { GitCommitFormPreviewContent() }
}
