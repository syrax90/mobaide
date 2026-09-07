package com.mobaide.app.ui.terminal.git

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
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
import com.mobaide.app.ui.common.pixel.PixelCard
import com.mobaide.app.ui.common.pixel.PixelChip
import com.mobaide.app.ui.common.pixel.PixelEmptyState
import com.mobaide.app.ui.common.pixel.PixelScaffold
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.PixelTopBar
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel

const val GIT_CHANGES_SCREEN_TAG = "git_changes_screen"
const val GIT_CHANGES_LOADING_TAG = "git_changes_loading"
const val GIT_CHANGES_EMPTY_TAG = "git_changes_empty"
const val GIT_CHANGES_ERROR_TAG = "git_changes_error"
const val GIT_CHANGES_OUTDATED_TAG = "git_changes_outdated"
const val GIT_CHANGES_UNAVAILABLE_TAG = "git_changes_unavailable"
const val GIT_CHANGES_SESSION_GONE_TAG = "git_changes_session_gone"
const val GIT_CHANGES_STALE_TAG = "git_changes_stale"

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GitChangesScreen(
    viewModel: GitChangesViewModel,
    onBack: () -> Unit,
    onOpenDiff: (sessionId: String, entry: GitStatusEntry, scope: String) -> Unit,
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()

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
            }
        }
    }

    PixelScaffold(
        topBar = {
            PixelTopBar(
                title = { Text("Changes") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { padding ->
        PullToRefreshBox(
            isRefreshing = (state as? GitChangesUiState.Ready)?.refreshing == true,
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
            )
        }
    }
}

/** `internal` so a test can compose every state without a ViewModel. */
@Composable
internal fun GitChangesContent(
    state: GitChangesUiState,
    onEntryClick: (GitStatusEntry) -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    when (state) {
        GitChangesUiState.Loading -> GitStateLoading(GIT_CHANGES_LOADING_TAG, modifier)
        is GitChangesUiState.Ready -> ChangesList(state, onEntryClick, onRetry, modifier)
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
    modifier: Modifier = Modifier,
) {
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(bottom = 24.dp),
    ) {
        state.staleError?.let { message ->
            item(key = "stale", contentType = "stale") { StaleBanner(message, onRetry) }
        }
        item(key = "header", contentType = "header") { RepoHeader(state.status, state.repo) }
        if (state.status.truncated) {
            item(key = "truncated", contentType = "truncated") {
                Text(
                    text = "Showing the first ${state.status.entry_limit} of " +
                        "${state.status.counts?.total ?: "many"} entries.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.pixel.warning,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }
        }
        if (state.sections.isEmpty()) {
            item(key = "empty", contentType = "empty") {
                PixelEmptyState(
                    text = "Working tree clean",
                    modifier = Modifier
                        .padding(horizontal = 16.dp, vertical = 12.dp)
                        .testTag(GIT_CHANGES_EMPTY_TAG),
                )
            }
        }
        state.sections.forEach { section ->
            stickyHeader(key = "section:${section.group}", contentType = "section") {
                SectionHeader(section)
            }
            items(
                items = section.entries,
                key = { "${section.group}:${it.path}" },
                contentType = { "entry" },
            ) { entry ->
                GitChangesRow(entry = entry, onClick = { onEntryClick(entry) })
            }
        }
    }
}

@Composable
private fun RepoHeader(status: GitStatusResponse, repo: GitRepoInfo) {
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
            upstreamLabel(repo.upstream)?.let { label ->
                Text(
                    text = label,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
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
private fun SectionHeader(section: GitChangesSection) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.background)
            .padding(horizontal = 16.dp, vertical = 8.dp),
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
        state = GitChangesUiState.Ready(status, status.repo!!, sectionsFor(status)),
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
