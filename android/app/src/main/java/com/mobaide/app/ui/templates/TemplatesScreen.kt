package com.mobaide.app.ui.templates

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.outlined.StarOutline
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.PixelScaffold
import com.mobaide.app.ui.common.pixel.PixelSnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTopBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import com.mobaide.app.ui.theme.ChromeFontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.TemplateDto

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TemplatesScreen(
    viewModel: TemplatesViewModel,
    onBack: () -> Unit,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(uiState.error) {
        uiState.error?.let { error ->
            snackbarHostState.showSnackbar(error)
            viewModel.clearError()
        }
    }

    val sections = remember(uiState.templates) { groupForManagement(uiState.templates) }

    PixelScaffold(
        topBar = {
            PixelTopBar(
                title = { Text("Prompt templates") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Back",
                        )
                    }
                },
            )
        },
        floatingActionButton = {
            FloatingActionButton(onClick = viewModel::openCreateEditor) {
                Icon(Icons.Default.Add, contentDescription = "New template")
            }
        },
        snackbarHost = { PixelSnackbarHost(snackbarHostState) },
    ) { padding ->
        PullToRefreshBox(
            isRefreshing = uiState.isLoading,
            onRefresh = viewModel::refresh,
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            if (uiState.templates.isEmpty() && !uiState.isLoading) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center,
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text = "No templates yet",
                            style = MaterialTheme.typography.titleMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(
                            text = "Save one from the input bar or tap +",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                    contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
                ) {
                    if (sections.favorites.isNotEmpty()) {
                        item(key = "header-favorites") {
                            SectionHeader("Favorites")
                        }
                        items(sections.favorites, key = { "fav-${it.template_id}" }) { template ->
                            TemplateCard(
                                template = template,
                                onEdit = { viewModel.openEditEditor(template) },
                                onDelete = { viewModel.showDeleteConfirm(template) },
                                onToggleFavorite = { viewModel.toggleFavorite(template) },
                            )
                        }
                    }
                    if (sections.others.isNotEmpty()) {
                        item(key = "header-all") {
                            SectionHeader("All")
                        }
                        items(sections.others, key = { "all-${it.template_id}" }) { template ->
                            TemplateCard(
                                template = template,
                                onEdit = { viewModel.openEditEditor(template) },
                                onDelete = { viewModel.showDeleteConfirm(template) },
                                onToggleFavorite = { viewModel.toggleFavorite(template) },
                            )
                        }
                    }
                }
            }
        }
    }

    val editor = uiState.editorState
    if (editor is EditorState.Open) {
        CreateOrEditTemplateDialog(
            target = editor.target,
            onSave = { name, body -> viewModel.saveFromEditor(name, body) },
            onDismiss = viewModel::dismissEditor,
        )
    }

    uiState.deleteTarget?.let { target ->
        DeleteTemplateDialog(
            name = target.name,
            onConfirm = { viewModel.confirmDelete(target) },
            onDismiss = viewModel::dismissDeleteConfirm,
        )
    }
}

@Composable
private fun SectionHeader(label: String) {
    Text(
        text = label,
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 8.dp, bottom = 4.dp),
    )
}

private data class ManagementSections(
    val favorites: List<TemplateDto>,
    val others: List<TemplateDto>,
)

private fun groupForManagement(templates: List<TemplateDto>): ManagementSections {
    val sortedFavorites = templates
        .filter { it.is_favorite }
        .sortedBy { it.name.lowercase() }
    val sortedOthers = templates
        .filter { !it.is_favorite }
        .sortedBy { it.name.lowercase() }
    return ManagementSections(sortedFavorites, sortedOthers)
}

@Composable
private fun TemplateCard(
    template: TemplateDto,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
    onToggleFavorite: () -> Unit,
) {
    var menuExpanded by remember { mutableStateOf(false) }
    Card(
        modifier = Modifier.fillMaxWidth(),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 4.dp, top = 12.dp, bottom = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = template.name,
                    style = MaterialTheme.typography.titleMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = template.body,
                    style = MaterialTheme.typography.bodySmall.copy(
                        fontFamily = ChromeFontFamily,
                    ),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            IconButton(onClick = onToggleFavorite) {
                Icon(
                    imageVector = if (template.is_favorite) Icons.Default.Star
                                  else Icons.Outlined.StarOutline,
                    contentDescription = if (template.is_favorite) "Unfavorite" else "Favorite",
                    tint = if (template.is_favorite) MaterialTheme.colorScheme.primary
                           else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Box {
                IconButton(onClick = { menuExpanded = true }) {
                    Icon(Icons.Default.MoreVert, contentDescription = "Options")
                }
                DropdownMenu(
                    expanded = menuExpanded,
                    onDismissRequest = { menuExpanded = false },
                ) {
                    DropdownMenuItem(
                        text = { Text("Edit") },
                        leadingIcon = { Icon(Icons.Default.Edit, contentDescription = null) },
                        onClick = {
                            menuExpanded = false
                            onEdit()
                        },
                    )
                    DropdownMenuItem(
                        text = {
                            Text("Delete", color = MaterialTheme.colorScheme.error)
                        },
                        leadingIcon = {
                            Icon(
                                Icons.Default.Delete,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.error,
                            )
                        },
                        onClick = {
                            menuExpanded = false
                            onDelete()
                        },
                    )
                }
            }
        }
    }
}
