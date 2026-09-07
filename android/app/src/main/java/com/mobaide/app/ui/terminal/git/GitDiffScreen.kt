package com.mobaide.app.ui.terminal.git

import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.GitDiffHunk
import com.mobaide.app.data.network.dto.GitDiffLine
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.ui.common.pixel.PixelCard
import com.mobaide.app.ui.common.pixel.PixelChip
import com.mobaide.app.ui.common.pixel.PixelScaffold
import com.mobaide.app.ui.common.pixel.PixelTopBar
import com.mobaide.app.ui.common.pixel.pixelChipBorder
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel

const val GIT_DIFF_SCREEN_TAG = "git_diff_screen"
const val GIT_DIFF_LOADING_TAG = "git_diff_loading"
const val GIT_DIFF_ERROR_TAG = "git_diff_error"
const val GIT_DIFF_OUTDATED_TAG = "git_diff_outdated"
const val GIT_DIFF_SESSION_GONE_TAG = "git_diff_session_gone"
const val GIT_DIFF_LIST_TAG = "git_diff_list"

fun gitDiffContextChipTag(lines: Int): String = "git_diff_context_$lines"

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GitDiffScreen(
    viewModel: GitDiffViewModel,
    onBack: () -> Unit,
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()

    DisposableEffect(viewModel) {
        viewModel.holdWatch()
        onDispose { viewModel.releaseWatch() }
    }

    PixelScaffold(
        topBar = {
            PixelTopBar(
                title = {
                    Text(
                        text = viewModel.path?.substringAfterLast('/') ?: "Diff",
                        style = MaterialTheme.typography.titleMedium.copy(fontFamily = ChromeFontFamily),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { padding ->
        PullToRefreshBox(
            isRefreshing = (state as? GitDiffUiState.Ready)?.refreshing == true,
            onRefresh = viewModel::refresh,
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .testTag(GIT_DIFF_SCREEN_TAG),
        ) {
            GitDiffContent(
                state = state,
                onSetContext = viewModel::setContext,
                onRetry = viewModel::refresh,
            )
        }
    }
}

/** `internal` so a test can compose every state without a ViewModel. */
@Composable
internal fun GitDiffContent(
    state: GitDiffUiState,
    onSetContext: (Int) -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    when (state) {
        is GitDiffUiState.Loading -> GitStateLoading(GIT_DIFF_LOADING_TAG, modifier)
        is GitDiffUiState.Ready -> Column(modifier.fillMaxSize()) {
            DiffHeader(state.diff, state.context, onSetContext)
            DiffBody(state, Modifier.weight(1f))
        }
        is GitDiffUiState.AgentOutdated -> GitStateMessage(
            title = "Update your agent",
            body = "This agent does not serve the git Changes API yet. " +
                "Update it to view diffs here.",
            tag = GIT_DIFF_OUTDATED_TAG,
            modifier = modifier,
        )
        is GitDiffUiState.SessionGone -> GitStateMessage(
            title = "Session ended",
            body = "This session is no longer running on the agent.",
            tag = GIT_DIFF_SESSION_GONE_TAG,
            modifier = modifier,
        )
        is GitDiffUiState.Error -> GitStateMessage(
            title = "Couldn't load diff",
            body = state.message,
            tag = GIT_DIFF_ERROR_TAG,
            actionLabel = "Retry",
            onAction = onRetry,
            modifier = modifier,
        )
    }
}

@Composable
private fun DiffHeader(diff: GitDiffResponse, context: Int, onSetContext: (Int) -> Unit) {
    PixelCard(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 12.dp),
    ) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(
                text = diff.path,
                style = MaterialTheme.typography.bodyMedium,
                fontFamily = ChromeFontFamily,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            diff.orig_path?.let { origPath ->
                Text(
                    text = "from $origPath",
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = ChromeFontFamily,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                diff.change?.let { change ->
                    PixelChip(changeLabel(change), tone = changeTone(change))
                }
                if (diff.kind == GitDiffResponse.KIND_TEXT || diff.kind == GitDiffResponse.KIND_SUBMODULE) {
                    Text(
                        text = "+${diff.additions}",
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.pixel.ok,
                    )
                    Text(
                        text = "−${diff.deletions}",
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.pixel.danger,
                    )
                }
                val oldMode = diff.old_mode
                val newMode = diff.new_mode
                if (oldMode != null && newMode != null && oldMode != newMode) {
                    Text(
                        text = "$oldMode → $newMode",
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = ChromeFontFamily,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Context".chromeCaps(),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                DIFF_CONTEXT_CHOICES.forEach { lines ->
                    ContextChip(
                        lines = lines,
                        selected = lines == context,
                        onClick = { onSetContext(lines) },
                    )
                }
            }
        }
    }
}

@Composable
private fun ContextChip(lines: Int, selected: Boolean, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        shape = RectangleShape,
        color = if (selected) MaterialTheme.colorScheme.surfaceContainerHighest else Color.Transparent,
        border = pixelChipBorder(selected = selected),
        modifier = Modifier
            .testTag(gitDiffContextChipTag(lines))
            .semantics { this.selected = selected },
    ) {
        Text(
            text = lines.toString(),
            style = MaterialTheme.typography.labelSmall,
            color = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
        )
    }
}

private fun changeLabel(change: String): String = when (change) {
    GitDiffResponse.CHANGE_TYPE_CHANGED -> "type changed"
    else -> change.replace('_', ' ')
}

@Composable
private fun changeTone(change: String): Color = when (change) {
    GitDiffResponse.CHANGE_ADDED -> MaterialTheme.pixel.ok
    GitDiffResponse.CHANGE_DELETED -> MaterialTheme.pixel.danger
    GitDiffResponse.CHANGE_RENAMED, GitDiffResponse.CHANGE_COPIED -> MaterialTheme.colorScheme.primary
    else -> MaterialTheme.pixel.warning
}

private data class DiffMetrics(val gutterWidth: Dp, val textWidth: Dp)

/** Well under the 2^18 px `Constraints` ceiling, whatever the font scale. */
private const val MAX_PINNED_TEXT_PX = 120_000

/**
 * One row per diff line, every row sharing one horizontal [ScrollState] over a
 * text column pinned to a single measured width. Identical content width plus
 * an identical viewport per row is what keeps the shared `maxValue` stable —
 * unpinned rows would each clamp the state to their own width. The line
 * number gutter stays put outside the scroller, and the add/del tint is painted
 * on the outer row so a short diff still colours the full width.
 */
@Composable
private fun DiffBody(state: GitDiffUiState.Ready, modifier: Modifier = Modifier) {
    val scrollState = rememberScrollState()
    val textMeasurer = rememberTextMeasurer()
    val density = LocalDensity.current
    val lineStyle = MaterialTheme.typography.bodySmall.copy(
        fontFamily = FontFamily.Monospace,
        fontSize = 13.sp,
        lineHeight = 18.sp,
    )
    val metrics = remember(state.widthCandidates, state.gutterDigits, lineStyle, density) {
        val cell = textMeasurer
            .measure(AnnotatedString("0"), style = lineStyle, softWrap = false)
            .size.width
        val widest = state.widthCandidates.maxOfOrNull { candidate ->
            textMeasurer
                .measure(AnnotatedString(candidate), style = lineStyle, softWrap = false)
                .size.width
        } ?: 0
        with(density) {
            DiffMetrics(
                gutterWidth = (cell * state.gutterDigits).toDp(),
                textWidth = (widest + cell).coerceAtMost(MAX_PINNED_TEXT_PX).toDp(),
            )
        }
    }
    LazyColumn(
        modifier = modifier
            .fillMaxSize()
            .testTag(GIT_DIFF_LIST_TAG),
        contentPadding = PaddingValues(bottom = 24.dp),
    ) {
        items(state.rows, key = { it.key }, contentType = { it::class }) { row ->
            when (row) {
                is DiffRow.HunkHeader -> HunkHeaderRow(row)
                is DiffRow.Line -> DiffLineRow(row, metrics, scrollState, lineStyle)
                is DiffRow.NoNewline -> NoNewlineRow(lineStyle)
                is DiffRow.Truncated -> NoticeRow("Truncated at ${row.shown} lines (${row.total} total)")
                is DiffRow.Notice -> NoticeRow(row.text)
            }
        }
    }
}

@Composable
private fun DiffLineRow(
    row: DiffRow.Line,
    metrics: DiffMetrics,
    scrollState: ScrollState,
    style: TextStyle,
) {
    val px = MaterialTheme.pixel
    val (foreground, background, marker) = when (row.type) {
        GitDiffLine.T_ADD -> Triple(px.ok, px.ok.copy(alpha = 0.12f), "+")
        GitDiffLine.T_DEL -> Triple(px.danger, px.danger.copy(alpha = 0.12f), "-")
        else -> Triple(MaterialTheme.colorScheme.onSurface, Color.Transparent, " ")
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(background),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        GutterCell(row.old, metrics.gutterWidth, style, Modifier.padding(start = 8.dp))
        GutterCell(row.new, metrics.gutterWidth, style, Modifier.padding(start = 8.dp))
        Text(
            text = marker,
            style = style,
            color = foreground,
            softWrap = false,
            modifier = Modifier.padding(horizontal = 6.dp),
        )
        Box(
            modifier = Modifier
                .weight(1f)
                .horizontalScroll(scrollState),
        ) {
            Text(
                text = row.text,
                style = style,
                color = foreground,
                softWrap = false,
                maxLines = 1,
                overflow = TextOverflow.Visible,
                modifier = Modifier
                    .padding(end = 12.dp)
                    .width(metrics.textWidth),
            )
        }
    }
}

@Composable
private fun GutterCell(number: Int?, width: Dp, style: TextStyle, modifier: Modifier = Modifier) {
    Text(
        text = number?.toString() ?: "",
        style = style,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        textAlign = TextAlign.End,
        softWrap = false,
        maxLines = 1,
        modifier = modifier.width(width),
    )
}

@Composable
private fun HunkHeaderRow(row: DiffRow.HunkHeader) {
    Text(
        text = if (row.heading.isBlank()) row.range else "${row.range}  ${row.heading}",
        style = MaterialTheme.typography.bodySmall,
        fontFamily = ChromeFontFamily,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .padding(horizontal = 12.dp, vertical = 6.dp),
    )
}

@Composable
private fun NoNewlineRow(style: TextStyle) {
    Text(
        text = "\\ No newline at end of file",
        style = style,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        softWrap = false,
        maxLines = 1,
        modifier = Modifier.padding(start = 12.dp),
    )
}

@Composable
private fun NoticeRow(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        textAlign = TextAlign.Center,
        modifier = Modifier
            .fillMaxWidth()
            .padding(16.dp),
    )
}

internal fun previewGitDiff(): GitDiffResponse = GitDiffResponse(
    path = "src/new name.py",
    orig_path = "src/old.py",
    scope = GitDiffResponse.SCOPE_STAGED,
    kind = GitDiffResponse.KIND_TEXT,
    change = GitDiffResponse.CHANGE_RENAMED,
    old_mode = "100644",
    new_mode = "100644",
    additions = 2,
    deletions = 1,
    hunks = listOf(
        GitDiffHunk(
            old_start = 6, old_lines = 4, new_start = 6, new_lines = 5, heading = "class Foo:",
            lines = listOf(
                GitDiffLine(GitDiffLine.T_CTX, 6, 6, "def foo():"),
                GitDiffLine(GitDiffLine.T_CTX, 7, 7, "\tx = 1"),
                GitDiffLine(GitDiffLine.T_DEL, 8, null, "    return x"),
                GitDiffLine(GitDiffLine.T_ADD, null, 8, "    y = 2  # a rather long trailing comment that overflows the phone width"),
                GitDiffLine(GitDiffLine.T_ADD, null, 9, "    return x + y", no_newline = true),
            ),
        ),
    ),
)

@Composable
private fun GitDiffPreviewContent() {
    val diff = previewGitDiff()
    val rows = buildDiffRows(diff)
    GitDiffContent(
        state = GitDiffUiState.Ready(diff, rows, gutterDigits(diff), widthCandidates(rows), DIFF_CONTEXT_DEFAULT),
        onSetContext = {},
        onRetry = {},
    )
}

@Preview(name = "Diff dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun GitDiffPreviewDark() {
    MobaideTheme(darkTheme = true) { GitDiffPreviewContent() }
}

@Preview(name = "Diff light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun GitDiffPreviewLight() {
    MobaideTheme(darkTheme = false) { GitDiffPreviewContent() }
}
