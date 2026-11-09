package com.mobaide.app.ui.sessions

import android.content.res.Configuration
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.ChatStoredSession
import com.mobaide.app.ui.chat.MiddleEllipsisText
import com.mobaide.app.ui.chat.markdown.CodeFontFamily
import com.mobaide.app.ui.chat.parseIsoMillis
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelCard
import com.mobaide.app.ui.common.pixel.PixelChip
import com.mobaide.app.ui.common.pixel.pixelSegmentedButtonColors
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

const val CHAT_RESUME_SHEET_TAG = "chat_resume_sheet"

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatResumeSheet(
    state: ChatResumeUiState,
    onSetAllFolders: (Boolean) -> Unit,
    onRetry: () -> Unit,
    onLoadMore: () -> Unit,
    onPick: (ChatStoredSession) -> Unit,
    onDismiss: () -> Unit,
) {
    PixelBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        ChatResumeContent(
            state = state,
            onSetAllFolders = onSetAllFolders,
            onRetry = onRetry,
            onLoadMore = onLoadMore,
            onPick = onPick,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatResumeContent(
    state: ChatResumeUiState,
    onSetAllFolders: (Boolean) -> Unit,
    onRetry: () -> Unit,
    onLoadMore: () -> Unit,
    onPick: (ChatStoredSession) -> Unit,
) {
    val now = remember(state.list) { System.currentTimeMillis() }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp)
            .testTag(CHAT_RESUME_SHEET_TAG),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(
                text = "Continue a session",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.semantics { heading() },
            )
            Text(
                text = if (state.live) {
                    "A session that is open on your PC stays in sync: both sides see every message."
                } else {
                    "If the session is still open in a terminal on your PC, close it there first."
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
            SegmentedButton(
                selected = !state.allFolders,
                onClick = { onSetAllFolders(false) },
                enabled = state.folder != null,
                shape = RectangleShape,
                colors = pixelSegmentedButtonColors(),
                icon = {},
            ) { Text("This folder") }
            SegmentedButton(
                selected = state.allFolders,
                onClick = { onSetAllFolders(true) },
                shape = RectangleShape,
                colors = pixelSegmentedButtonColors(),
                icon = {},
            ) { Text("All folders") }
        }
        when (val list = state.list) {
            ChatResumeListState.Loading -> CenteredStatus {
                PixelBlinkCursor()
                Spacer(modifier = Modifier.height(12.dp))
                Text(
                    text = "Looking for sessions…",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            ChatResumeListState.Empty -> CenteredStatus {
                Text(
                    text = if (state.allFolders) "No earlier sessions." else "No earlier sessions in this folder.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                )
            }
            is ChatResumeListState.Error -> CenteredStatus {
                Text(
                    text = list.message,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.pixel.danger,
                    textAlign = TextAlign.Center,
                )
                Spacer(modifier = Modifier.height(12.dp))
                PixelButton(text = "Retry", onClick = onRetry)
            }
            is ChatResumeListState.Loaded -> LazyColumn(
                modifier = Modifier.heightIn(max = 560.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = PaddingValues(bottom = 24.dp, end = 4.dp),
            ) {
                items(list.rows, key = { it.session_id }) { row ->
                    StoredSessionRow(row = row, showFolder = state.allFolders, nowMs = now, onClick = { onPick(row) })
                }
                if (list.nextCursor != null || list.loadMoreError != null) {
                    item(key = "load-more") {
                        Column(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalAlignment = Alignment.CenterHorizontally,
                        ) {
                            list.loadMoreError?.let {
                                Text(
                                    text = it,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.pixel.danger,
                                    textAlign = TextAlign.Center,
                                )
                                Spacer(modifier = Modifier.height(8.dp))
                            }
                            if (list.loadingMore) {
                                PixelBlinkCursor(modifier = Modifier.padding(12.dp))
                            } else if (list.nextCursor != null) {
                                PixelButton(text = "Load more", onClick = onLoadMore)
                            }
                        }
                    }
                }
            }
        }
        if (state.list !is ChatResumeListState.Loaded) Spacer(modifier = Modifier.height(16.dp))
    }
}

@Composable
private fun CenteredStatus(content: @Composable () -> Unit) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 32.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) { content() }
    }
}

@Composable
private fun StoredSessionRow(
    row: ChatStoredSession,
    showFolder: Boolean,
    nowMs: Long,
    onClick: () -> Unit,
) {
    PixelCard(modifier = Modifier.fillMaxWidth(), onClick = onClick) {
        Column(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = row.title?.takeIf { it.isNotBlank() } ?: "Untitled session",
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (row.open_session_id != null) {
                    Spacer(modifier = Modifier.width(8.dp))
                    PixelChip("Open", tone = MaterialTheme.pixel.ok)
                }
            }
            relativeTime(row.updated_at, nowMs)?.let {
                Text(
                    text = it,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (row.open_elsewhere && row.open_session_id == null) {
                Text(
                    text = "Open in another app on your PC. Close it there first.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.pixel.warning,
                )
            }
            if (showFolder) {
                MiddleEllipsisText(
                    text = row.cwd,
                    style = MaterialTheme.typography.bodySmall.copy(fontFamily = CodeFontFamily),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

/** "Just now", "5 min ago", "3 h ago", "Yesterday", "4 days ago", then a date. */
internal fun relativeTime(iso: String?, nowMs: Long, zone: ZoneId = ZoneId.systemDefault()): String? {
    val thenMs = parseIsoMillis(iso) ?: return null
    val minutes = (nowMs - thenMs) / 60_000
    if (minutes < 1) return "Just now"
    if (minutes < 60) return "$minutes min ago"
    val today = Instant.ofEpochMilli(nowMs).atZone(zone).toLocalDate()
    val day = Instant.ofEpochMilli(thenMs).atZone(zone).toLocalDate()
    if (day == today) return "${minutes / 60} h ago"
    if (day == today.minusDays(1)) return "Yesterday"
    val days = today.toEpochDay() - day.toEpochDay()
    if (days in 2..6) return "$days days ago"
    val pattern = if (day.year == LocalDate.now(zone).year) "MMM d" else "MMM d, yyyy"
    return day.format(DateTimeFormatter.ofPattern(pattern, Locale.US))
}

@Preview(name = "Resume list light", showBackground = true)
@Preview(name = "Resume list dark", showBackground = true, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
private fun ChatResumeContentPreview() {
    MobaideTheme {
        ChatResumeContent(
            state = ChatResumeUiState(
                folder = "/home/user/proj",
                allFolders = true,
                list = ChatResumeListState.Loaded(
                    rows = listOf(
                        ChatStoredSession("a", "/home/user/proj", "Refactor the login flow", null, "s1"),
                        ChatStoredSession("b", "/home/user/other", null, null, null),
                    ),
                    nextCursor = "offset:50",
                ),
            ),
            onSetAllFolders = {},
            onRetry = {},
            onLoadMore = {},
            onPick = {},
        )
    }
}
