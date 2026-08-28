package com.mobaide.app.ui.projects.git

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.BorderStroke
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.PixelCheckbox
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import com.mobaide.app.ui.theme.ChromeFontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.GitItem

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun GitItemRow(
    item: GitItem,
    selected: Boolean,
    inSelectionMode: Boolean,
    onTap: () -> Unit,
    onLongPress: () -> Unit,
) {
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 4.dp)
            .combinedClickable(
                onClick = onTap,
                onLongClick = onLongPress,
            ),
        border = BorderStroke(
            2.dp,
            if (selected) MaterialTheme.pixel.brand2 else MaterialTheme.pixel.surface2,
        ),
        color = if (selected) {
            MaterialTheme.colorScheme.surfaceContainerHighest
        } else {
            MaterialTheme.colorScheme.surfaceContainerLow
        },
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (inSelectionMode && item.isDeletable) {
                PixelCheckbox(checked = selected, onCheckedChange = { onTap() })
            }
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = primaryLabel(item),
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = FontWeight.Medium,
                )
                secondaryLabel(item)?.let { secondary ->
                    Text(
                        text = secondary,
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = ChromeFontFamily,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                val chips = chipsFor(item)
                if (chips.isNotEmpty()) {
                    Box(modifier = Modifier.padding(top = 4.dp)) {
                        ChipRow(chips)
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ChipRow(chips: List<Chip>) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        chips.forEach { chip ->
            Surface(
                color = MaterialTheme.colorScheme.surfaceContainerHighest,
                border = BorderStroke(2.dp, chip.ring),
                modifier = Modifier.padding(vertical = 2.dp),
            ) {
                Text(
                    text = chip.text.chromeCaps(),
                    style = MaterialTheme.typography.labelSmall,
                    color = chip.contentColor,
                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                )
            }
        }
    }
}

private data class Chip(val text: String, val ring: Color, val contentColor: Color)

private fun primaryLabel(item: GitItem): String = when (item.kind) {
    GitItem.KIND_BRANCH_WITH_WORKTREE, GitItem.KIND_BRANCH_ONLY ->
        item.branch.orEmpty()
    GitItem.KIND_ORPHAN_WORKTREE ->
        item.worktree_path.orEmpty()
    else -> item.branch ?: item.worktree_path.orEmpty()
}

private fun secondaryLabel(item: GitItem): String? = when (item.kind) {
    GitItem.KIND_BRANCH_WITH_WORKTREE -> item.worktree_path
    GitItem.KIND_ORPHAN_WORKTREE -> if (item.detached) "(detached HEAD)" else null
    else -> null
}

@Composable
private fun chipsFor(item: GitItem): List<Chip> {
    val cs = MaterialTheme.colorScheme
    val px = MaterialTheme.pixel
    val out = mutableListOf<Chip>()
    if (item.is_current) {
        out.add(Chip("current", px.brand2, cs.primary))
    }
    if (item.gone) {
        out.add(Chip("gone", cs.error, cs.error))
    }
    if (item.ahead > 0 || item.behind > 0) {
        out.add(
            Chip(
                "ahead ${item.ahead} / behind ${item.behind}",
                cs.outline,
                cs.onSurface,
            )
        )
    }
    if (item.locked) {
        val text = item.locked_reason?.takeIf { it.isNotBlank() }
            ?.let { "locked: $it" } ?: "locked"
        out.add(Chip(text, cs.error, cs.error))
    }
    if (item.kind == GitItem.KIND_ORPHAN_WORKTREE) {
        out.add(Chip("orphan", cs.error, cs.error))
    }
    if (item.prunable) {
        out.add(Chip("prunable", px.rule, cs.onSurfaceVariant))
    }
    return out
}
