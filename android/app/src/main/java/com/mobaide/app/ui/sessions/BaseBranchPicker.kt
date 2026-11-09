package com.mobaide.app.ui.sessions

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.WorktreeBaseRef
import com.mobaide.app.data.network.dto.WorktreeBasesResponse
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import com.mobaide.app.ui.common.pixel.PixelChip
import com.mobaide.app.ui.common.pixel.PixelIconButton
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel
import kotlinx.coroutines.launch
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime

const val BASE_BRANCH_FIELD_TAG = "base_branch_field"
const val BASE_BRANCH_ERROR_TAG = "base_branch_error"
const val BASE_BRANCH_RETRY_TAG = "base_branch_retry"
const val BASE_BRANCH_SHEET_TAG = "base_branch_sheet"
const val BASE_BRANCH_FILTER_TAG = "base_branch_filter"
const val BASE_BRANCH_DEFAULT_ROW_TAG = "base_branch_row_default"
const val BASE_BRANCH_ROW_TAG_PREFIX = "base_branch_row_"

internal const val DEFAULT_BRANCH_LABEL = "Default branch"

/** A case-insensitive substring match on the ref name; a blank query keeps every ref. */
internal fun filterBaseRefs(refs: List<WorktreeBaseRef>, query: String): List<WorktreeBaseRef> {
    val needle = query.trim()
    if (needle.isEmpty()) return refs
    return refs.filter { it.ref.contains(needle, ignoreCase = true) }
}

/** "5m ago" from the agent's ISO-8601 commit time, or null when it is absent or unreadable. */
internal fun relativeCommitTime(iso: String?, now: Instant = Instant.now()): String? {
    if (iso.isNullOrBlank()) return null
    val then = runCatching { OffsetDateTime.parse(iso).toInstant() }.getOrNull() ?: return null
    val seconds = Duration.between(then, now).seconds.coerceAtLeast(0)
    return when {
        seconds < 60 -> "just now"
        seconds < 3_600 -> "${seconds / 60}m ago"
        seconds < 86_400 -> "${seconds / 3_600}h ago"
        seconds < 30 * 86_400 -> "${seconds / 86_400}d ago"
        seconds < 365 * 86_400 -> "${seconds / (30 * 86_400)}mo ago"
        else -> "${seconds / (365 * 86_400)}y ago"
    }
}

/**
 * The "Base branch" field under the worktree branch name. Tapping it opens
 * [BaseBranchSheet]; while the list loads it is disabled, and a failed load
 * adds an inline Retry (creating still works and uses the agent's default).
 */
@Composable
internal fun BaseBranchField(
    load: BaseBranchesLoad,
    selected: String?,
    onOpen: () -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val loaded = load is BaseBranchesLoad.Loaded
    val value = when (load) {
        BaseBranchesLoad.Loading, BaseBranchesLoad.Idle -> "Loading branches…"
        else -> selected ?: DEFAULT_BRANCH_LABEL
    }
    Column(modifier = modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Box {
            OutlinedTextField(
                colors = PixelTextFieldDefaults.colors(),
                value = value,
                onValueChange = {},
                readOnly = true,
                enabled = loaded,
                singleLine = true,
                label = { Text("Base branch") },
                trailingIcon = { Icon(Icons.Default.KeyboardArrowDown, contentDescription = null) },
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag(BASE_BRANCH_FIELD_TAG),
            )
            // A read-only text field keeps its taps for the cursor; this layer takes them.
            Box(
                modifier = Modifier
                    .matchParentSize()
                    .clickable(
                        enabled = loaded,
                        role = Role.DropdownList,
                        onClickLabel = "Choose base branch",
                        onClick = onOpen,
                    ),
            )
        }
        if (load is BaseBranchesLoad.Failed) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.testTag(BASE_BRANCH_ERROR_TAG),
            ) {
                Text(
                    text = "Couldn't load branches",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.weight(1f),
                )
                PixelTextButton(onClick = onRetry, modifier = Modifier.testTag(BASE_BRANCH_RETRY_TAG)) {
                    Text("Retry")
                }
            }
        }
    }
}

/**
 * The searchable base-branch picker. A pick animates the sheet away before
 * [onDismiss]: an M3 sheet that is removed without hiding skips its exit.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun BaseBranchSheet(
    response: WorktreeBasesResponse,
    selected: String?,
    onSelect: (String?) -> Unit,
    onDismiss: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    PixelBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        modifier = Modifier.testTag(BASE_BRANCH_SHEET_TAG),
    ) {
        BaseBranchList(
            response = response,
            selected = selected,
            onSelect = { ref ->
                onSelect(ref)
                scope.launch { sheetState.hide() }.invokeOnCompletion { onDismiss() }
            },
            onClose = {
                scope.launch { sheetState.hide() }.invokeOnCompletion { onDismiss() }
            },
        )
    }
}

/** The sheet body; `internal` so a test can compose it without a sheet window. */
@Composable
internal fun BaseBranchList(
    response: WorktreeBasesResponse,
    selected: String?,
    onSelect: (String?) -> Unit,
    onClose: () -> Unit,
    modifier: Modifier = Modifier,
    now: Instant = remember { Instant.now() },
) {
    var query by rememberSaveable { mutableStateOf("") }
    val filtered = remember(response.refs, query) { filterBaseRefs(response.refs, query) }
    Column(
        modifier = modifier
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
                text = "Base branch",
                style = MaterialTheme.typography.titleMedium.copy(fontFamily = ChromeFontFamily),
                color = MaterialTheme.colorScheme.primary,
            )
            PixelIconButton(Icons.Default.Close, "Close", onClick = onClose)
        }
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = query,
            onValueChange = { query = it },
            placeholder = { Text("Filter branches") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.None,
                autoCorrectEnabled = false,
            ),
            modifier = Modifier
                .fillMaxWidth()
                .testTag(BASE_BRANCH_FILTER_TAG),
        )
        LazyColumn(
            modifier = Modifier.heightIn(max = 480.dp),
            contentPadding = PaddingValues(bottom = 16.dp),
        ) {
            if (query.isBlank()) {
                item(key = "synthetic:default") {
                    BaseBranchRow(
                        title = DEFAULT_BRANCH_LABEL,
                        subtitle = "Let the agent choose",
                        isDefault = false,
                        isLocal = false,
                        selected = selected == null,
                        onClick = { onSelect(null) },
                        modifier = Modifier.testTag(BASE_BRANCH_DEFAULT_ROW_TAG),
                    )
                }
            }
            items(filtered, key = { "ref:${it.kind}:${it.ref}" }) { ref ->
                BaseBranchRow(
                    title = ref.ref,
                    subtitle = relativeCommitTime(ref.committed_at, now),
                    isDefault = ref.ref == response.default,
                    isLocal = ref.isLocal,
                    selected = ref.ref == selected,
                    onClick = { onSelect(ref.ref) },
                    modifier = Modifier.testTag(BASE_BRANCH_ROW_TAG_PREFIX + ref.ref),
                )
            }
            if (filtered.isEmpty()) {
                item(key = "synthetic:empty") {
                    Text(
                        text = if (response.refs.isEmpty()) "No branches." else "No matches.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(vertical = 24.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun BaseBranchRow(
    title: String,
    subtitle: String?,
    isDefault: Boolean,
    isLocal: Boolean,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .semantics { this.selected = selected }
            .clickable(role = Role.RadioButton, onClick = onClick)
            .padding(vertical = 8.dp),
    ) {
        Box(modifier = Modifier.size(24.dp), contentAlignment = Alignment.Center) {
            if (selected) {
                Icon(
                    Icons.Default.Check,
                    contentDescription = "Selected",
                    tint = MaterialTheme.pixel.accent,
                )
            }
        }
        Spacer(Modifier.width(8.dp))
        Column(modifier = Modifier.weight(1f)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (selected) MaterialTheme.pixel.accent else MaterialTheme.colorScheme.onSurface,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false),
                )
                if (isDefault) PixelChip("default", tone = MaterialTheme.pixel.ok)
                if (isLocal) PixelChip("local", tone = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (subtitle != null) {
                Text(
                    text = subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

private val previewResponse = WorktreeBasesResponse(
    default = "origin/main",
    last_used = "origin/develop",
    refs = listOf(
        WorktreeBaseRef("origin/main", WorktreeBaseRef.KIND_REMOTE, committed_at = "2026-11-08T10:15:00+00:00"),
        WorktreeBaseRef("origin/develop", WorktreeBaseRef.KIND_REMOTE, committed_at = "2026-11-07T18:02:11+01:00"),
        WorktreeBaseRef("wip/local", WorktreeBaseRef.KIND_LOCAL),
    ),
)

@Composable
private fun BaseBranchPreviewContent() {
    Column(verticalArrangement = Arrangement.spacedBy(16.dp), modifier = Modifier.padding(vertical = 16.dp)) {
        Column(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            BaseBranchField(BaseBranchesLoad.Loaded(previewResponse), "origin/develop", {}, {})
            BaseBranchField(BaseBranchesLoad.Loading, null, {}, {})
            BaseBranchField(BaseBranchesLoad.Failed, null, {}, {})
        }
        BaseBranchList(
            response = previewResponse,
            selected = "origin/develop",
            onSelect = {},
            onClose = {},
            now = Instant.parse("2026-11-09T12:00:00Z"),
        )
    }
}

@Preview(name = "Base branch dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun BaseBranchPreviewDark() {
    MobaideTheme(darkTheme = true) { BaseBranchPreviewContent() }
}

@Preview(name = "Base branch light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun BaseBranchPreviewLight() {
    MobaideTheme(darkTheme = false) { BaseBranchPreviewContent() }
}
