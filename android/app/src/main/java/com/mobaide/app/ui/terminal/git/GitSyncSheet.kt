package com.mobaide.app.ui.terminal.git

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.GitJobResponse
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelProgressBar
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

const val GIT_SYNC_SHEET_TAG = "git_sync_sheet"
const val GIT_SYNC_ERROR_TAG = "git_sync_error"
const val GIT_SYNC_PROGRESS_TAG = "git_sync_progress"
const val GIT_SYNC_RESULT_TAG = "git_sync_result"
const val GIT_SYNC_CANCEL_TAG = "git_sync_cancel"
const val GIT_SYNC_CONFIRM_DIALOG_TAG = "git_sync_confirm_dialog"

fun gitSyncRowTag(op: String): String = "git_sync_row_$op"

fun gitSyncFollowUpTag(followUp: SyncFollowUp): String = "git_sync_follow_up_${followUp.name}"

/**
 * The Fetch / Pull / Push sheet: the head and upstream line, one row per
 * action with its reason when it cannot run, then — once a job exists — its
 * progress with Cancel, or its outcome with the follow-ups a failure allows.
 * The sheet may be closed while a job runs (the list's banner carries on), so
 * only the start request itself blocks dismissal.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun GitSyncSheet(
    state: SyncSheetState,
    repo: GitRepoInfo?,
    capabilities: GitCapabilities,
    banner: GitJobBanner?,
    onDismiss: () -> Unit,
    onStart: (op: String) -> Unit,
    onCancelJob: () -> Unit,
    onFollowUp: (SyncFollowUp) -> Unit,
) {
    PixelBottomSheet(
        onDismissRequest = { if (!state.starting) onDismiss() },
        sheetState = rememberModalBottomSheetState(
            skipPartiallyExpanded = true,
            confirmValueChange = { !state.starting },
        ),
        modifier = Modifier.testTag(GIT_SYNC_SHEET_TAG),
    ) {
        GitSyncForm(
            state = state,
            repo = repo,
            capabilities = capabilities,
            banner = banner,
            onDismiss = onDismiss,
            onStart = onStart,
            onCancelJob = onCancelJob,
            onFollowUp = onFollowUp,
        )
    }
}

/** `internal` so a test can compose the form without a sheet window. */
@Composable
internal fun GitSyncForm(
    state: SyncSheetState,
    repo: GitRepoInfo?,
    capabilities: GitCapabilities,
    banner: GitJobBanner?,
    onDismiss: () -> Unit,
    onStart: (op: String) -> Unit,
    onCancelJob: () -> Unit,
    onFollowUp: (SyncFollowUp) -> Unit,
    modifier: Modifier = Modifier,
) {
    val running = banner?.running == true
    val locked = state.starting || running
    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(start = 20.dp, end = 20.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = "Fetch / Pull / Push",
            style = MaterialTheme.typography.titleLarge.copy(fontFamily = ChromeFontFamily),
            color = MaterialTheme.colorScheme.primary,
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = repo?.let(::headLabel) ?: "—",
                style = MaterialTheme.typography.titleMedium.copy(fontFamily = ChromeFontFamily),
            )
            Text(
                text = upstreamLabel(repo?.upstream) ?: "No upstream",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        syncRows(repo, capabilities).forEach { row ->
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                PixelButton(
                    text = row.label,
                    onClick = { onStart(row.op) },
                    enabled = row.enabled && !locked,
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag(gitSyncRowTag(row.op)),
                )
                Text(
                    text = row.detail,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        state.error?.let { error ->
            Text(
                text = error,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.testTag(GIT_SYNC_ERROR_TAG),
            )
        }
        if (banner != null) {
            if (banner.running) {
                JobProgress(banner, onCancelJob)
            } else {
                JobResult(banner, repo, locked, capabilities.conflicts, onFollowUp)
            }
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp, Alignment.End),
        ) {
            PixelButton(text = "Close", onClick = onDismiss, enabled = !state.starting)
        }
    }
}

@Composable
private fun JobProgress(banner: GitJobBanner, onCancelJob: () -> Unit) {
    val job = banner.job
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .padding(12.dp)
            .testTag(GIT_SYNC_PROGRESS_TAG),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(
                text = job?.let { jobTitle(it.op) } ?: "Syncing…",
                style = MaterialTheme.typography.titleSmall,
            )
            job?.phase?.let { phase ->
                Text(
                    text = job.percent?.let { "$phase $it%" } ?: phase,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        PixelProgressBar(progress = job?.percent?.let { it / 100f })
        job?.line?.takeIf { it.isNotEmpty() }?.let { line ->
            Text(
                text = line,
                style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp, Alignment.End),
        ) {
            PixelButton(
                text = "Cancel",
                onClick = onCancelJob,
                modifier = Modifier.testTag(GIT_SYNC_CANCEL_TAG),
            )
        }
    }
}

@Composable
private fun JobResult(
    banner: GitJobBanner,
    repo: GitRepoInfo?,
    locked: Boolean,
    canResolveInApp: Boolean,
    onFollowUp: (SyncFollowUp) -> Unit,
) {
    val job = banner.job
    val ok = !banner.failed
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .padding(12.dp)
            .testTag(GIT_SYNC_RESULT_TAG),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(
            text = when {
                banner.lost -> JOB_LOST_COPY
                job == null -> "Syncing…"
                else -> jobDoneTitle(job.op, ok)
            },
            style = MaterialTheme.typography.titleSmall,
            color = if (ok) MaterialTheme.pixel.ok else MaterialTheme.colorScheme.error,
        )
        if (job != null) {
            if (ok) {
                job.message?.takeIf { it.isNotEmpty() }?.let { message ->
                    Text(
                        text = message,
                        style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                Text(
                    text = jobErrorCopy(job.error_code, job.message, canResolveInApp),
                    style = MaterialTheme.typography.bodySmall,
                )
                if (showsJobOutput(job)) {
                    Text(
                        text = job.message.orEmpty(),
                        style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.heightIn(max = 160.dp),
                    )
                }
                val followUps = jobFollowUps(job, repo)
                if (followUps.isNotEmpty()) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(12.dp, Alignment.End),
                    ) {
                        followUps.forEach { followUp ->
                            PixelButton(
                                text = followUpLabel(followUp),
                                onClick = { onFollowUp(followUp) },
                                style = if (followUp == SyncFollowUp.ForcePush) PixelButtonStyle.Danger
                                else PixelButtonStyle.Ghost,
                                enabled = !locked,
                                modifier = Modifier.testTag(gitSyncFollowUpTag(followUp)),
                            )
                        }
                    }
                }
            }
        }
    }
}

/** The confirmation every follow-up (and a rebase's Skip / Abort) goes through, in the discard dialog's shape. */
@Composable
internal fun GitSyncConfirmDialog(
    prompt: SyncPrompt,
    destructive: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    tag: String = GIT_SYNC_CONFIRM_DIALOG_TAG,
) {
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        modifier = Modifier.testTag(tag),
        title = { Text(prompt.title) },
        text = { Text(prompt.body) },
        confirmButton = {
            PixelTextButton(onClick = onConfirm) {
                Text(
                    prompt.confirm,
                    color = if (destructive) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                )
            }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

@Composable
private fun GitSyncPreviewContent() {
    val status = previewGitStatus()
    GitSyncForm(
        state = SyncSheetState(),
        repo = status.repo,
        capabilities = GitCapabilities(fetch = true, pull = true, push = true),
        banner = GitJobBanner(
            jobId = "job",
            job = GitJobResponse(
                job_id = "job",
                session_id = "s1",
                op = GitJobResponse.OP_PUSH,
                phase = "Writing objects",
                percent = 45,
                line = "Writing objects:  45% (14/32)",
            ),
        ),
        onDismiss = {},
        onStart = {},
        onCancelJob = {},
        onFollowUp = {},
    )
}

@Preview(name = "Sync sheet dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun GitSyncPreviewDark() {
    MobaideTheme(darkTheme = true) { GitSyncPreviewContent() }
}

@Preview(name = "Sync sheet light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun GitSyncPreviewLight() {
    MobaideTheme(darkTheme = false) { GitSyncPreviewContent() }
}
