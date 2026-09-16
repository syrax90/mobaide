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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.PixelCheckbox
import com.mobaide.app.ui.common.pixel.PixelIconButton
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import com.mobaide.app.ui.theme.ChromeFontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.GitItem

const val GIT_ROW_ACTIONS_TAG = "git_row_actions"
const val GIT_ROW_CHECKOUT_TAG = "git_row_checkout"

/**
 * One list row. The overflow menu carries Check out for the rows that can
 * take it (`GitItem.canCheckOut`) once the agent advertises the capability;
 * selection mode hides it so the row is one tap target.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun GitItemRow(
    item: GitItem,
    selected: Boolean,
    inSelectionMode: Boolean,
    onTap: () -> Unit,
    onLongPress: () -> Unit,
    modifier: Modifier = Modifier,
    checkoutEnabled: Boolean = false,
    busy: Boolean = false,
    onCheckout: () -> Unit = {},
) {
    Surface(
        modifier = modifier
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
                tipLine(item)?.let { tip ->
                    Text(
                        text = tip,
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = ChromeFontFamily,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                    )
                }
                val chips = chipsFor(item)
                if (chips.isNotEmpty()) {
                    Box(modifier = Modifier.padding(top = 4.dp)) {
                        ChipRow(chips)
                    }
                }
            }
            if (checkoutEnabled && item.canCheckOut && !inSelectionMode) {
                RowActions(busy = busy, onCheckout = onCheckout)
            }
        }
    }
}

@Composable
private fun RowActions(busy: Boolean, onCheckout: () -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        PixelIconButton(
            imageVector = Icons.Default.MoreVert,
            contentDescription = "More actions",
            onClick = { expanded = true },
            enabled = !busy,
            compact = true,
            modifier = Modifier.testTag(GIT_ROW_ACTIONS_TAG),
        )
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            DropdownMenuItem(
                text = { Text("Check out…") },
                onClick = {
                    expanded = false
                    onCheckout()
                },
                modifier = Modifier.testTag(GIT_ROW_CHECKOUT_TAG),
            )
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
    if (item.kind == GitItem.KIND_REMOTE_BRANCH) {
        out.add(Chip(item.remote ?: "remote", px.rule, cs.onSurfaceVariant))
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
