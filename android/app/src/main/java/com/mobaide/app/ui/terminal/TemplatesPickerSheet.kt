package com.mobaide.app.ui.terminal

import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.outlined.StarOutline
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.ui.templates.DeleteTemplateDialog

private const val MAX_RECENTS = 5

private data class GroupedTemplates(
    val recents: List<TemplateDto>,
    val favorites: List<TemplateDto>,
    val others: List<TemplateDto>,
) {
    fun isEmpty(): Boolean = recents.isEmpty() && favorites.isEmpty() && others.isEmpty()
}

private fun groupTemplates(
    all: List<TemplateDto>,
    query: String,
): GroupedTemplates {
    val q = query.trim()
    val filtered = if (q.isEmpty()) all else all.filter {
        it.name.contains(q, ignoreCase = true)
            || it.body.contains(q, ignoreCase = true)
    }
    val recents = filtered
        .filter { it.last_used_at != null }
        .sortedByDescending { it.last_used_at }
        .take(MAX_RECENTS)
    val recentIds = recents.map { it.template_id }.toSet()
    val favorites = filtered
        .filter { it.is_favorite && it.template_id !in recentIds }
        .sortedBy { it.name.lowercase() }
    val favoriteIds = favorites.map { it.template_id }.toSet()
    val others = filtered
        .filterNot { it.template_id in recentIds || it.template_id in favoriteIds }
        .sortedBy { it.name.lowercase() }
    return GroupedTemplates(recents, favorites, others)
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalComposeUiApi::class)
@Composable
fun TemplatesPickerSheet(
    templates: List<TemplateDto>,
    onPick: (TemplateDto) -> Unit,
    onToggleFavorite: (TemplateDto) -> Unit,
    onDelete: (TemplateDto) -> Unit,
    onDismiss: () -> Unit,
) {
    var query by remember { mutableStateOf("") }
    var pendingDelete by remember { mutableStateOf<TemplateDto?>(null) }
    val grouped = remember(templates, query) { groupTemplates(templates, query) }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .imePadding()
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Prompt templates",
                    style = MaterialTheme.typography.titleMedium,
                )
                IconButton(onClick = onDismiss) {
                    Icon(Icons.Default.Close, contentDescription = "Close")
                }
            }
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                modifier = Modifier.fillMaxWidth(),
                placeholder = { Text("Search templates") },
                singleLine = true,
            )
            if (templates.isEmpty()) {
                Text(
                    text = "You haven't saved any templates yet. Type a prompt and tap the bookmark icon to save it.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(vertical = 24.dp),
                )
            } else if (grouped.isEmpty()) {
                Text(
                    text = "No matches.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(vertical = 24.dp),
                )
            } else {
                LazyColumn(
                    modifier = Modifier.heightIn(max = 480.dp),
                    contentPadding = PaddingValues(bottom = 16.dp),
                ) {
                    sectionItems("Recents", grouped.recents, onPick, onToggleFavorite) {
                        pendingDelete = it
                    }
                    sectionItems("Favorites", grouped.favorites, onPick, onToggleFavorite) {
                        pendingDelete = it
                    }
                    sectionItems("All", grouped.others, onPick, onToggleFavorite) {
                        pendingDelete = it
                    }
                }
            }
        }
    }

    pendingDelete?.let { target ->
        DeleteTemplateDialog(
            name = target.name,
            onConfirm = {
                onDelete(target)
                pendingDelete = null
            },
            onDismiss = { pendingDelete = null },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalComposeUiApi::class)
private fun androidx.compose.foundation.lazy.LazyListScope.sectionItems(
    label: String,
    items: List<TemplateDto>,
    onPick: (TemplateDto) -> Unit,
    onToggleFavorite: (TemplateDto) -> Unit,
    onLongPressDelete: (TemplateDto) -> Unit,
) {
    if (items.isEmpty()) return
    item(key = "header-$label") {
        Text(
            text = label,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 8.dp, bottom = 4.dp),
        )
    }
    items(items, key = { "row-$label-${it.template_id}" }) { template ->
        TemplateRow(
            template = template,
            onPick = { onPick(template) },
            onToggleFavorite = { onToggleFavorite(template) },
            onLongPress = { onLongPressDelete(template) },
        )
        HorizontalDivider()
    }
}

@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
private fun TemplateRow(
    template: TemplateDto,
    onPick: () -> Unit,
    onToggleFavorite: () -> Unit,
    onLongPress: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .combinedClickable(
                onClick = onPick,
                onLongClick = onLongPress,
            )
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = template.name,
                style = MaterialTheme.typography.titleSmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = template.body,
                style = MaterialTheme.typography.bodySmall.copy(
                    fontFamily = FontFamily.Monospace,
                ),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        IconButton(onClick = onToggleFavorite) {
            Box(modifier = Modifier.size(24.dp)) {
                Icon(
                    imageVector = if (template.is_favorite) Icons.Default.Star
                                  else Icons.Outlined.StarOutline,
                    contentDescription = if (template.is_favorite) "Unfavorite" else "Favorite",
                    tint = if (template.is_favorite) MaterialTheme.colorScheme.primary
                           else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
