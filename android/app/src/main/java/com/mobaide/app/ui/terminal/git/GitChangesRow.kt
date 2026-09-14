package com.mobaide.app.ui.terminal.git

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.ui.common.pixel.PixelChip
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

fun gitChangesRowTag(entry: GitStatusEntry): String = "git_changes_row_${entry.group}_${entry.path}"

@Composable
internal fun GitChangesRow(
    entry: GitStatusEntry,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val badge = badgeFor(entry)
    Row(
        modifier = modifier
            .fillMaxWidth()
            .testTag(gitChangesRowTag(entry))
            .clickable(onClick = onClick)
            .heightIn(min = 48.dp)
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        StatusBadgeChip(badge)
        Spacer(Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = entry.fileName,
                style = MaterialTheme.typography.bodyLarge,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            val secondary = entry.orig_path?.let { "$it → ${entry.path}" } ?: entry.directory
            if (secondary != null) {
                Text(
                    text = secondary,
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = ChromeFontFamily,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            if (entry.submodule) {
                Row(modifier = Modifier.padding(top = 4.dp)) {
                    PixelChip("submodule", tone = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable
private fun StatusBadgeChip(badge: StatusBadge) {
    val tone = badge.tone.color()
    Surface(
        color = tone.copy(alpha = 0.14f),
        border = BorderStroke(2.dp, tone),
        shape = RectangleShape,
    ) {
        Text(
            text = badge.label,
            style = MaterialTheme.typography.labelSmall,
            color = tone,
            textAlign = TextAlign.Center,
            modifier = Modifier
                .widthIn(min = 28.dp)
                .padding(horizontal = 6.dp, vertical = 3.dp),
        )
    }
}

@Composable
internal fun StatusBadgeTone.color(): Color = when (this) {
    StatusBadgeTone.Added -> MaterialTheme.pixel.ok
    StatusBadgeTone.Deleted -> MaterialTheme.pixel.danger
    StatusBadgeTone.Modified -> MaterialTheme.pixel.warning
    StatusBadgeTone.Renamed -> MaterialTheme.colorScheme.primary
    StatusBadgeTone.Conflict -> MaterialTheme.colorScheme.error
    StatusBadgeTone.Untracked -> MaterialTheme.pixel.gray
}

@Composable
private fun GitChangesRowPreviewContent() {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        GitChangesRow(
            entry = GitStatusEntry(
                group = GitStatusEntry.GROUP_STAGED, path = "src/new name.py",
                orig_path = "src/old.py", code = "R", index_status = "R",
                worktree_status = ".", rename_score = 90,
            ),
            onClick = {},
        )
        GitChangesRow(
            entry = GitStatusEntry(
                group = GitStatusEntry.GROUP_UNSTAGED, path = "src/both.py",
                code = "M", index_status = "M", worktree_status = "M",
            ),
            onClick = {},
        )
        GitChangesRow(
            entry = GitStatusEntry(
                group = GitStatusEntry.GROUP_CONFLICTS, path = "src/conflict.py",
                code = "UU", index_status = "U", worktree_status = "U",
            ),
            onClick = {},
        )
        GitChangesRow(
            entry = GitStatusEntry(
                group = GitStatusEntry.GROUP_STAGED, path = "vendor/lib",
                code = "M", index_status = "M", worktree_status = ".", submodule = true,
            ),
            onClick = {},
        )
        GitChangesRow(
            entry = GitStatusEntry(
                group = GitStatusEntry.GROUP_UNTRACKED, path = "notes/тест.md",
                code = "?", index_status = "?", worktree_status = "?",
            ),
            onClick = {},
        )
    }
}

@Preview(name = "Changes rows dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun GitChangesRowPreviewDark() {
    MobaideTheme(darkTheme = true) { GitChangesRowPreviewContent() }
}

@Preview(name = "Changes rows light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun GitChangesRowPreviewLight() {
    MobaideTheme(darkTheme = false) { GitChangesRowPreviewContent() }
}
