package com.mobaide.app.ui.terminal.git

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Checklist
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material.icons.outlined.Commit
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.GitHeadInfo
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusCounts
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.GitUpstreamInfo
import com.mobaide.app.data.network.dto.gitPathDirectory
import com.mobaide.app.data.network.dto.gitPathFileName
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelCard
import com.mobaide.app.ui.common.pixel.PixelChip
import com.mobaide.app.ui.common.pixel.PixelEmptyState
import com.mobaide.app.ui.common.pixel.PixelIconButton
import com.mobaide.app.ui.common.pixel.PixelProgressBar
import com.mobaide.app.ui.common.pixel.PixelScaffold
import com.mobaide.app.ui.common.pixel.PixelSnackbarHost
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.PixelTopBar
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel
import kotlinx.coroutines.launch

const val GIT_CHANGES_SCREEN_TAG = "git_changes_screen"
const val GIT_CHANGES_LOADING_TAG = "git_changes_loading"
const val GIT_CHANGES_EMPTY_TAG = "git_changes_empty"
const val GIT_CHANGES_ERROR_TAG = "git_changes_error"
const val GIT_CHANGES_OUTDATED_TAG = "git_changes_outdated"
const val GIT_CHANGES_UNAVAILABLE_TAG = "git_changes_unavailable"
const val GIT_CHANGES_SESSION_GONE_TAG = "git_changes_session_gone"
const val GIT_CHANGES_STALE_TAG = "git_changes_stale"
const val GIT_CHANGES_COMMIT_TAG = "git_changes_commit"
const val GIT_CHANGES_SELECT_TAG = "git_changes_select"
const val GIT_CHANGES_SELECTION_STAGE_TAG = "git_changes_selection_stage"
const val GIT_CHANGES_SELECTION_UNSTAGE_TAG = "git_changes_selection_unstage"
const val GIT_CHANGES_SELECTION_DISCARD_TAG = "git_changes_selection_discard"
const val GIT_CHANGES_DISCARD_DIALOG_TAG = "git_changes_discard_dialog"
const val GIT_CHANGES_RESTORE_DIALOG_TAG = "git_changes_restore_dialog"
const val GIT_CHANGES_RESOLVED_SECTION_TAG = "git_changes_resolved_section"

fun gitChangesRestoreTag(path: String) = "git_changes_restore_$path"
const val GIT_CHANGES_SYNC_TAG = "git_changes_sync"
const val GIT_CHANGES_UPSTREAM_TAG = "git_changes_upstream"
const val GIT_CHANGES_JOB_TAG = "git_changes_job"
const val GIT_CHANGES_JOB_CANCEL_TAG = "git_changes_job_cancel"
const val GIT_CHANGES_JOB_OPTIONS_TAG = "git_changes_job_options"
const val GIT_CHANGES_JOB_DISMISS_TAG = "git_changes_job_dismiss"
const val GIT_CHANGES_REBASE_TAG = "git_changes_rebase"
const val GIT_CHANGES_REBASE_CONTINUE_TAG = "git_changes_rebase_continue"
const val GIT_CHANGES_REBASE_SKIP_TAG = "git_changes_rebase_skip"
const val GIT_CHANGES_REBASE_ABORT_TAG = "git_changes_rebase_abort"
const val GIT_CHANGES_REBASE_DIALOG_TAG = "git_changes_rebase_dialog"

fun gitChangesSectionActionTag(group: String): String = "git_changes_section_action_$group"

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GitChangesScreen(
    viewModel: GitChangesViewModel,
    onBack: () -> Unit,
    onOpenDiff: (sessionId: String, entry: GitStatusEntry, scope: String) -> Unit,
    onOpenConflict: (sessionId: String, path: String) -> Unit = { _, _ -> },
    /** What the conflict resolver left behind on closing, shown once. */
    resultMessage: String? = null,
    onResultMessageShown: () -> Unit = {},
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val discardPrompt by viewModel.discardPrompt.collectAsStateWithLifecycle()
    val restorePrompt by viewModel.restorePrompt.collectAsStateWithLifecycle()
    val commitSheet by viewModel.commitSheet.collectAsStateWithLifecycle()
    val syncSheet by viewModel.syncSheet.collectAsStateWithLifecycle()
    val rebaseSheet by viewModel.rebaseSheet.collectAsStateWithLifecycle()
    val rebasePrompt by viewModel.rebasePrompt.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    // Keeps the covered terminal's socket attached for exactly as long as this
    // screen is composed.
    DisposableEffect(viewModel) {
        viewModel.holdWatch()
        onDispose { viewModel.releaseWatch() }
    }

    LaunchedEffect(viewModel) {
        viewModel.events.collect { event ->
            when (event) {
                is GitChangesEvent.OpenDiff -> onOpenDiff(event.sessionId, event.entry, event.scope)
                is GitChangesEvent.OpenConflict -> onOpenConflict(event.sessionId, event.path)
                // Its own coroutine: `showSnackbar` suspends for the snackbar's
                // lifetime and must not hold up the next navigation event.
                is GitChangesEvent.ShowMessage -> launch { snackbarHostState.showSnackbar(event.text) }
            }
        }
    }

    LaunchedEffect(resultMessage) {
        if (resultMessage != null) {
            viewModel.showMessage(resultMessage)
            onResultMessageShown()
        }
    }

    val ready = state as? GitChangesUiState.Ready
    val inSelectionMode = ready?.inSelectionMode == true
    BackHandler(enabled = inSelectionMode) { viewModel.clearSelection() }

    PixelScaffold(
        topBar = {
            if (ready != null && inSelectionMode) {
                SelectionTopBar(
                    state = ready,
                    onClose = viewModel::clearSelection,
                    onStage = viewModel::stageSelection,
                    onUnstage = viewModel::unstageSelection,
                    onDiscard = viewModel::discardSelection,
                )
            } else {
                PixelTopBar(
                    title = { Text("Changes") },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                        }
                    },
                    actions = {
                        if (ready != null && ready.hasActionableRows) {
                            PixelIconButton(
                                imageVector = Icons.Default.Checklist,
                                contentDescription = "Select files",
                                onClick = viewModel::enterSelection,
                                enabled = !ready.busy,
                                modifier = Modifier.testTag(GIT_CHANGES_SELECT_TAG),
                            )
                        }
                        if (ready != null && ready.capabilities.commit) {
                            PixelIconButton(
                                imageVector = Icons.Outlined.Commit,
                                contentDescription = "Commit",
                                onClick = viewModel::openCommit,
                                enabled = ready.stagedCount > 0 && !ready.busy,
                                modifier = Modifier.testTag(GIT_CHANGES_COMMIT_TAG),
                            )
                        }
                        if (ready != null && ready.capabilities.hasSync) {
                            PixelIconButton(
                                imageVector = Icons.Outlined.Sync,
                                contentDescription = "Sync",
                                onClick = viewModel::openSync,
                                modifier = Modifier.testTag(GIT_CHANGES_SYNC_TAG),
                            )
                        }
                    },
                )
            }
        },
        snackbarHost = { PixelSnackbarHost(snackbarHostState) },
    ) { padding ->
        PullToRefreshBox(
            isRefreshing = ready?.refreshing == true,
            onRefresh = viewModel::refresh,
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .testTag(GIT_CHANGES_SCREEN_TAG),
        ) {
            GitChangesContent(
                state = state,
                onEntryClick = viewModel::onEntryClick,
                onRetry = viewModel::refresh,
                onEntryLongClick = viewModel::onEntryLongClick,
                onStage = viewModel::stage,
                onUnstage = viewModel::unstage,
                onDiscard = viewModel::requestDiscard,
                onSync = if (ready?.capabilities?.hasSync == true) viewModel::openSync else null,
                onCancelJob = viewModel::cancelSync,
                onDismissJob = viewModel::dismissJobResult,
                onResolve = viewModel::resolve,
                onContinueRebase = viewModel::continueRebase,
                onRebaseStep = viewModel::requestRebaseStep,
                onRestore = viewModel::requestRestore,
            )
        }
    }

    restorePrompt?.let { path ->
        val prompt = restorePrompt(path)
        PixelAlertDialog(
            onDismissRequest = viewModel::dismissRestore,
            modifier = Modifier.testTag(GIT_CHANGES_RESTORE_DIALOG_TAG),
            title = { Text(prompt.title) },
            text = { Text(prompt.body) },
            confirmButton = {
                PixelTextButton(onClick = viewModel::confirmRestore, enabled = ready?.busy != true) {
                    Text(prompt.confirm, color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                PixelTextButton(onClick = viewModel::dismissRestore) { Text("Cancel") }
            },
        )
    }

    discardPrompt?.let { prompt ->
        GitDiscardDialog(
            prompt = prompt,
            busy = ready?.busy == true,
            onConfirm = viewModel::confirmDiscard,
            onDismiss = viewModel::dismissDiscard,
        )
    }

    commitSheet?.let { sheet ->
        GitCommitSheet(
            state = sheet,
            stagedCount = ready?.stagedCount ?: 0,
            onDismiss = viewModel::closeCommit,
            onCommit = viewModel::commit,
        )
    }

    rebaseSheet?.let { sheet ->
        GitRebaseSheet(
            state = sheet,
            status = ready?.status,
            onDismiss = viewModel::closeRebase,
            onRebase = viewModel::startRebase,
        )
    }

    rebasePrompt?.let { step ->
        GitSyncConfirmDialog(
            prompt = rebaseStepPrompt(step, ready?.repo),
            destructive = true,
            onConfirm = viewModel::confirmRebaseStep,
            onDismiss = viewModel::dismissRebaseStep,
            tag = GIT_CHANGES_REBASE_DIALOG_TAG,
        )
    }

    syncSheet?.let { sheet ->
        GitSyncSheet(
            state = sheet,
            repo = ready?.repo,
            capabilities = ready?.capabilities ?: GitCapabilities.NONE,
            banner = ready?.job,
            onDismiss = viewModel::closeSync,
            onStart = viewModel::startSync,
            onCancelJob = viewModel::cancelSync,
            onFollowUp = viewModel::requestFollowUp,
        )
        sheet.pendingFollowUp?.let { followUp ->
            GitSyncConfirmDialog(
                prompt = followUpPrompt(followUp, ready?.repo),
                destructive = followUp == SyncFollowUp.ForcePush,
                onConfirm = viewModel::confirmFollowUp,
                onDismiss = viewModel::dismissFollowUp,
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SelectionTopBar(
    state: GitChangesUiState.Ready,
    onClose: () -> Unit,
    onStage: () -> Unit,
    onUnstage: () -> Unit,
    onDiscard: () -> Unit,
) {
    val subsets = selectionSubsets(state.sections, state.selectedKeys, state.capabilities)
    PixelTopBar(
        title = {
            Text(if (state.selectedKeys.isEmpty()) "Select files" else "${state.selectedKeys.size} selected")
        },
        navigationIcon = {
            IconButton(onClick = onClose) {
                Icon(Icons.Default.Close, contentDescription = "Exit selection")
            }
        },
        actions = {
            if (state.capabilities.stage) {
                PixelIconButton(
                    imageVector = Icons.Default.Add,
                    contentDescription = "Stage selected",
                    onClick = onStage,
                    enabled = subsets.stage.isNotEmpty() && !state.busy,
                    modifier = Modifier.testTag(GIT_CHANGES_SELECTION_STAGE_TAG),
                )
                PixelIconButton(
                    imageVector = Icons.Default.Remove,
                    contentDescription = "Unstage selected",
                    onClick = onUnstage,
                    enabled = subsets.unstage.isNotEmpty() && !state.busy,
                    modifier = Modifier.testTag(GIT_CHANGES_SELECTION_UNSTAGE_TAG),
                )
            }
            if (state.capabilities.discard) {
                PixelIconButton(
                    imageVector = Icons.Default.Delete,
                    contentDescription = "Discard selected",
                    onClick = onDiscard,
                    enabled = subsets.discard.isNotEmpty() && !state.busy,
                    modifier = Modifier.testTag(GIT_CHANGES_SELECTION_DISCARD_TAG),
                )
            }
        },
    )
}

@Composable
internal fun GitDiscardDialog(
    prompt: DiscardPrompt,
    busy: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        modifier = Modifier.testTag(GIT_CHANGES_DISCARD_DIALOG_TAG),
        title = { Text(prompt.title) },
        text = { Text(prompt.body) },
        confirmButton = {
            PixelTextButton(onClick = onConfirm, enabled = !busy) {
                Text("Discard", color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

/** `internal` so a test can compose every state without a ViewModel. */
@Composable
internal fun GitChangesContent(
    state: GitChangesUiState,
    onEntryClick: (GitStatusEntry) -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
    onEntryLongClick: (GitStatusEntry) -> Unit = {},
    onStage: (List<GitStatusEntry>) -> Unit = {},
    onUnstage: (List<GitStatusEntry>) -> Unit = {},
    onDiscard: (List<GitStatusEntry>) -> Unit = {},
    /** Opens the Fetch / Pull / Push sheet; null on an agent without the jobs. */
    onSync: (() -> Unit)? = null,
    onCancelJob: () -> Unit = {},
    onDismissJob: () -> Unit = {},
    onResolve: (GitStatusEntry) -> Unit = {},
    onContinueRebase: () -> Unit = {},
    onRebaseStep: (RebasePromptStep) -> Unit = {},
    onRestore: (String) -> Unit = {},
) {
    when (state) {
        GitChangesUiState.Loading -> GitStateLoading(GIT_CHANGES_LOADING_TAG, modifier)
        is GitChangesUiState.Ready -> ChangesList(
            state = state,
            onEntryClick = onEntryClick,
            onRetry = onRetry,
            onEntryLongClick = onEntryLongClick,
            onStage = onStage,
            onUnstage = onUnstage,
            onDiscard = onDiscard,
            onSync = onSync,
            onCancelJob = onCancelJob,
            onDismissJob = onDismissJob,
            onResolve = onResolve,
            onContinueRebase = onContinueRebase,
            onRebaseStep = onRebaseStep,
            onRestore = onRestore,
            modifier = modifier,
        )
        is GitChangesUiState.Unavailable -> {
            val (title, body) = unavailableCopy(state.reason)
            GitStateMessage(
                title = title,
                body = body,
                detail = state.cwd,
                tag = GIT_CHANGES_UNAVAILABLE_TAG,
                modifier = modifier,
            )
        }
        GitChangesUiState.AgentOutdated -> GitStateMessage(
            title = "Update your agent",
            body = "This agent does not serve the git Changes API yet. " +
                "Update it to view changes here.",
            tag = GIT_CHANGES_OUTDATED_TAG,
            modifier = modifier,
        )
        GitChangesUiState.SessionGone -> GitStateMessage(
            title = "Session ended",
            body = "This session is no longer running on the agent.",
            tag = GIT_CHANGES_SESSION_GONE_TAG,
            modifier = modifier,
        )
        is GitChangesUiState.Error -> GitStateMessage(
            title = "Couldn't load changes",
            body = state.message,
            tag = GIT_CHANGES_ERROR_TAG,
            actionLabel = "Retry",
            onAction = onRetry,
            modifier = modifier,
        )
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ChangesList(
    state: GitChangesUiState.Ready,
    onEntryClick: (GitStatusEntry) -> Unit,
    onRetry: () -> Unit,
    onEntryLongClick: (GitStatusEntry) -> Unit,
    onStage: (List<GitStatusEntry>) -> Unit,
    onUnstage: (List<GitStatusEntry>) -> Unit,
    onDiscard: (List<GitStatusEntry>) -> Unit,
    onSync: (() -> Unit)?,
    onCancelJob: () -> Unit,
    onDismissJob: () -> Unit,
    onResolve: (GitStatusEntry) -> Unit,
    onContinueRebase: () -> Unit,
    onRebaseStep: (RebasePromptStep) -> Unit,
    onRestore: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val canResolveInApp = state.capabilities.conflicts
    // *Resolved* sits right under *Conflicts*: it is where a conflict row goes.
    val resolvedPaths = if (canResolveInApp) state.status.resolved_paths else emptyList()
    val resolvedAfter = state.sections.indexOfFirst { it.group == GitStatusEntry.GROUP_CONFLICTS }
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(bottom = 24.dp),
    ) {
        state.staleError?.let { message ->
            item(key = "stale", contentType = "stale") { StaleBanner(message, onRetry) }
        }
        state.job?.let { banner ->
            item(key = "job", contentType = "job") {
                SyncJobBanner(
                    banner = banner,
                    canResolveInApp = canResolveInApp,
                    onCancel = onCancelJob,
                    onOptions = onSync,
                    onDismiss = onDismissJob,
                )
            }
        }
        rebaseBanner(state.repo, state.status.counts?.conflicts ?: 0, canResolveInApp)?.let { banner ->
            item(key = "rebase", contentType = "rebase") {
                RebaseBannerRow(
                    banner = banner,
                    actionable = state.capabilities.rebase && banner.actionable,
                    canContinue = !state.busy && (state.status.counts?.conflicts ?: 0) == 0,
                    busy = state.busy,
                    onContinue = onContinueRebase,
                    onStep = onRebaseStep,
                )
            }
        }
        item(key = "header", contentType = "header") {
            RepoHeader(state.status, state.repo, onSync)
        }
        if (state.status.truncated) {
            item(key = "truncated", contentType = "truncated") {
                Text(
                    text = truncatedNotice(state.status),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.pixel.warning,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }
        }
        if (state.sections.isEmpty() && resolvedPaths.isEmpty()) {
            item(key = "empty", contentType = "empty") {
                PixelEmptyState(
                    text = "Working tree clean",
                    modifier = Modifier
                        .padding(horizontal = 16.dp, vertical = 12.dp)
                        .testTag(GIT_CHANGES_EMPTY_TAG),
                )
            }
        }
        if (resolvedAfter < 0) resolvedSection(resolvedPaths, state.busy, onRestore)
        state.sections.forEachIndexed { sectionIndex, section ->
            stickyHeader(key = "section:${section.group}", contentType = "section") {
                SectionHeader(
                    section = section,
                    action = sectionAction(section, state.capabilities)
                        ?.takeUnless { state.inSelectionMode },
                    enabled = !state.busy,
                    onAction = { stage ->
                        if (stage) onStage(section.entries) else onUnstage(section.entries)
                    },
                )
            }
            itemsIndexed(
                items = section.entries,
                key = { index, _ -> section.keys[index] },
                contentType = { _, _ -> "entry" },
            ) { index, entry ->
                GitChangesRow(
                    entry = entry,
                    onClick = { onEntryClick(entry) },
                    actions = rowActions(entry, state.capabilities),
                    inSelectionMode = state.inSelectionMode,
                    selected = section.keys[index] in state.selectedKeys,
                    busy = state.busy,
                    onLongClick = { onEntryLongClick(entry) },
                    onStage = { onStage(listOf(entry)) },
                    onUnstage = { onUnstage(listOf(entry)) },
                    onDiscard = { onDiscard(listOf(entry)) },
                    onResolve = { onResolve(entry) },
                )
            }
            if (sectionIndex == resolvedAfter) resolvedSection(resolvedPaths, state.busy, onRestore)
        }
    }
}

/**
 * Files marked resolved during the operation in progress. Not entries — a
 * resolution equal to HEAD has no status row — so they are never selectable
 * and open nothing; the one action rebuilds the conflict.
 */
@OptIn(ExperimentalFoundationApi::class)
private fun LazyListScope.resolvedSection(
    paths: List<String>,
    busy: Boolean,
    onRestore: (String) -> Unit,
) {
    if (paths.isEmpty()) return
    stickyHeader(key = "section:resolved", contentType = "section") {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.background)
                .padding(horizontal = 16.dp, vertical = 4.dp)
                .heightIn(min = 40.dp)
                .testTag(GIT_CHANGES_RESOLVED_SECTION_TAG),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "Resolved".chromeCaps(),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            Text(
                text = paths.size.toString(),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
    items(items = paths, key = { "resolved:$it" }, contentType = { "resolved" }) { path ->
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = gitPathFileName(path),
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                gitPathDirectory(path)?.let { dir ->
                    Text(
                        text = dir,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            PixelTextButton(
                onClick = { onRestore(path) },
                enabled = !busy,
                modifier = Modifier.testTag(gitChangesRestoreTag(path)),
            ) { Text("Restore conflict…") }
        }
    }
}

@Composable
private fun RepoHeader(status: GitStatusResponse, repo: GitRepoInfo, onSync: (() -> Unit)?) {
    PixelCard(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 12.dp),
    ) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = headLabel(repo),
                    style = MaterialTheme.typography.titleMedium.copy(fontFamily = ChromeFontFamily),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                repoStateLabel(repo.state)?.let { label ->
                    Spacer(Modifier.width(8.dp))
                    PixelChip(label, tone = MaterialTheme.pixel.warning)
                }
                if (repo.is_linked_worktree) {
                    Spacer(Modifier.width(8.dp))
                    PixelChip("worktree", tone = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            // The ahead/behind counts are what a fetch, pull or push acts on,
            // so the line itself opens the sheet once the agent can run them.
            upstreamLabel(repo.upstream)?.let { label ->
                Text(
                    text = label,
                    style = MaterialTheme.typography.bodySmall,
                    color = if (onSync == null) MaterialTheme.colorScheme.onSurfaceVariant
                    else MaterialTheme.colorScheme.primary,
                    modifier = Modifier
                        .testTag(GIT_CHANGES_UPSTREAM_TAG)
                        .let { base -> if (onSync == null) base else base.clickable(onClick = onSync) },
                )
            }
            status.cwd?.let { cwd ->
                Text(
                    text = cwd,
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = ChromeFontFamily,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

@Composable
private fun SectionHeader(
    section: GitChangesSection,
    action: SectionAction? = null,
    enabled: Boolean = true,
    onAction: (stage: Boolean) -> Unit = {},
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.background)
            .padding(start = 16.dp, end = if (action == null) 16.dp else 4.dp, top = 4.dp, bottom = 4.dp)
            .heightIn(min = 40.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = section.title.chromeCaps(),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        Text(
            text = section.entries.size.toString(),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (action != null) {
            Spacer(Modifier.width(8.dp))
            PixelTextButton(
                onClick = { onAction(action.stage) },
                enabled = enabled,
                modifier = Modifier.testTag(gitChangesSectionActionTag(section.group)),
            ) { Text(action.label) }
        }
    }
}

/**
 * A running fetch / pull / push, or its result until dismissed: the title
 * and git's latest line with the bar while it runs, Cancel beside it; the
 * outcome afterwards, with Options… leading back to the sheet's follow-ups
 * when it failed.
 */
@Composable
private fun SyncJobBanner(
    banner: GitJobBanner,
    canResolveInApp: Boolean,
    onCancel: () -> Unit,
    onOptions: (() -> Unit)?,
    onDismiss: () -> Unit,
) {
    val job = banner.job
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .padding(start = 16.dp, end = 4.dp, top = 6.dp, bottom = 6.dp)
            .testTag(GIT_CHANGES_JOB_TAG),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(
                text = banner.text(canResolveInApp),
                style = MaterialTheme.typography.bodySmall,
                color = when {
                    banner.failed -> MaterialTheme.colorScheme.error
                    banner.running -> MaterialTheme.colorScheme.onSurface
                    else -> MaterialTheme.pixel.ok
                },
            )
            if (banner.running && job != null) {
                PixelProgressBar(progress = job.percent?.let { it / 100f })
                job.line?.takeIf { it.isNotEmpty() }?.let { line ->
                    Text(
                        text = line,
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
        if (banner.running) {
            PixelTextButton(
                onClick = onCancel,
                modifier = Modifier.testTag(GIT_CHANGES_JOB_CANCEL_TAG),
            ) { Text("Cancel") }
        } else {
            if (banner.failed && onOptions != null) {
                PixelTextButton(
                    onClick = onOptions,
                    modifier = Modifier.testTag(GIT_CHANGES_JOB_OPTIONS_TAG),
                ) { Text("Options…") }
            }
            IconButton(
                onClick = onDismiss,
                modifier = Modifier.testTag(GIT_CHANGES_JOB_DISMISS_TAG),
            ) {
                Icon(Icons.Default.Close, contentDescription = "Dismiss")
            }
        }
    }
}

/**
 * A rebase in progress, for as long as the status says so: where it stopped
 * and what to do next, with Continue (once no conflict is left) and the two
 * confirmed ways out.
 */
@Composable
private fun RebaseBannerRow(
    banner: RebaseBanner,
    actionable: Boolean,
    canContinue: Boolean,
    busy: Boolean,
    onContinue: () -> Unit,
    onStep: (RebasePromptStep) -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .padding(start = 16.dp, end = 4.dp, top = 8.dp, bottom = 4.dp)
            .testTag(GIT_CHANGES_REBASE_TAG),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(
            text = banner.title,
            style = MaterialTheme.typography.bodyMedium.copy(fontFamily = ChromeFontFamily),
            color = MaterialTheme.pixel.warning,
            modifier = Modifier.padding(end = 12.dp),
        )
        banner.progress?.let { progress ->
            Text(
                text = progress,
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(end = 12.dp),
            )
        }
        Text(
            text = banner.hint,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(end = 12.dp),
        )
        if (actionable) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End,
            ) {
                PixelTextButton(
                    onClick = { onStep(RebasePromptStep.Abort) },
                    enabled = !busy,
                    modifier = Modifier.testTag(GIT_CHANGES_REBASE_ABORT_TAG),
                ) { Text("Abort…", color = MaterialTheme.colorScheme.error) }
                PixelTextButton(
                    onClick = { onStep(RebasePromptStep.Skip) },
                    enabled = !busy,
                    modifier = Modifier.testTag(GIT_CHANGES_REBASE_SKIP_TAG),
                ) { Text("Skip…") }
                PixelTextButton(
                    onClick = onContinue,
                    enabled = canContinue,
                    modifier = Modifier.testTag(GIT_CHANGES_REBASE_CONTINUE_TAG),
                ) { Text("Continue") }
            }
        }
    }
}

@Composable
private fun StaleBanner(message: String, onRetry: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .padding(start = 16.dp, end = 4.dp)
            .testTag(GIT_CHANGES_STALE_TAG),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = message,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
            modifier = Modifier.weight(1f),
        )
        PixelTextButton(onClick = onRetry) { Text("Retry") }
    }
}

internal fun previewGitStatus(): GitStatusResponse = GitStatusResponse(
    session_id = "s1",
    cwd = "/home/user/proj/.wt/feature-x",
    etag = "3f0d4c1e",
    repo = GitRepoInfo(
        toplevel = "/home/user/proj/.wt/feature-x",
        git_dir = "/home/user/proj/.git/worktrees/feature-x",
        common_dir = "/home/user/proj/.git",
        is_linked_worktree = true,
        head = GitHeadInfo(oid = "0123456789abcdef", short_oid = "0123456", branch = "feature/x"),
        upstream = GitUpstreamInfo(ref = "origin/feature/x", ahead = 2, behind = 0),
        state = GitRepoInfo.STATE_MERGING,
    ),
    entries = listOf(
        GitStatusEntry(
            group = GitStatusEntry.GROUP_CONFLICTS, path = "src/conflict.py",
            code = "UU", index_status = "U", worktree_status = "U",
        ),
        GitStatusEntry(
            group = GitStatusEntry.GROUP_STAGED, path = "src/new name.py", orig_path = "src/old.py",
            code = "R", index_status = "R", worktree_status = ".", rename_score = 90,
        ),
        GitStatusEntry(
            group = GitStatusEntry.GROUP_STAGED, path = "vendor/lib",
            code = "M", index_status = "M", worktree_status = ".", submodule = true,
        ),
        GitStatusEntry(
            group = GitStatusEntry.GROUP_UNSTAGED, path = "src/both.py",
            code = "M", index_status = "M", worktree_status = "M",
        ),
        GitStatusEntry(
            group = GitStatusEntry.GROUP_UNTRACKED, path = "notes/тест.md",
            code = "?", index_status = "?", worktree_status = "?",
        ),
    ),
    counts = GitStatusCounts(conflicts = 1, staged = 2, unstaged = 1, untracked = 1, total = 5),
)

@Composable
private fun GitChangesPreviewContent() {
    val status = previewGitStatus()
    GitChangesContent(
        state = GitChangesUiState.Ready(
            status = status,
            repo = status.repo!!,
            sections = sectionsFor(status),
            capabilities = GitCapabilities(stage = true, discard = true, commit = true),
        ),
        onEntryClick = {},
        onRetry = {},
    )
}

@Preview(name = "Changes dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun GitChangesPreviewDark() {
    MobaideTheme(darkTheme = true) { GitChangesPreviewContent() }
}

@Preview(name = "Changes light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun GitChangesPreviewLight() {
    MobaideTheme(darkTheme = false) { GitChangesPreviewContent() }
}
