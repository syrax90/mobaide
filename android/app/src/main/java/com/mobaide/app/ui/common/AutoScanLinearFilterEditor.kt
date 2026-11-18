package com.mobaide.app.ui.common

import android.content.res.Configuration
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.LinearLabelDto
import com.mobaide.app.data.network.dto.LinearStateDto
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import com.mobaide.app.ui.theme.MobaideTheme

/** Per-field messages of [AutoScanLinearFilterEditor]; null hides a field's message. */
data class LinearFilterErrors(
    val triggerLabels: String? = null,
    val triggerStates: String? = null,
    val activeState: String? = null,
    val doneState: String? = null,
    val removalLabel: String? = null,
)

/**
 * The Auto Scan Linear filter: trigger labels and states (multi), the active
 * and done states, and the removal label. Shared by the workspace form (older
 * agents) and the new-session wizard. Chips show the union of what Linear
 * returned and what is selected, so saved choices stay visible before the
 * discovery call lands or when it fails.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AutoScanLinearFilterEditor(
    availableLabels: List<LinearLabelDto>,
    availableStates: List<LinearStateDto>,
    triggerLabels: Set<String>,
    triggerStates: Set<String>,
    activeState: String,
    doneState: String,
    removalLabel: String,
    loading: Boolean,
    loadError: String?,
    errors: LinearFilterErrors,
    onToggleTriggerLabel: (String) -> Unit,
    onToggleTriggerState: (String) -> Unit,
    onActiveStateChange: (String) -> Unit,
    onDoneStateChange: (String) -> Unit,
    onRemovalLabelChange: (String) -> Unit,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier,
    title: String = "Auto Scan filters",
    description: String = "Each matching issue runs in its own git worktree " +
        "(branched per Linear issue), so its changes stay isolated " +
        "from your working copy. Workspaces that aren't git repositories " +
        "run in the workspace directory directly.",
) {
    val labelNames = remember(availableLabels, triggerLabels, removalLabel) {
        val seen = LinkedHashSet<String>()
        availableLabels.forEach { seen.add(it.name) }
        seen.addAll(triggerLabels)
        if (removalLabel.isNotBlank()) seen.add(removalLabel)
        seen.toList()
    }
    val stateNames = remember(availableStates, triggerStates, activeState, doneState) {
        val seen = LinkedHashSet<String>()
        availableStates.forEach { seen.add(it.name) }
        seen.addAll(triggerStates)
        if (activeState.isNotBlank()) seen.add(activeState)
        if (doneState.isNotBlank()) seen.add(doneState)
        seen.toList()
    }
    val stateByName = remember(availableStates) { availableStates.associateBy { it.name } }

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = title,
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f),
            )
            IconButton(onClick = onRefresh, enabled = !loading) {
                Icon(Icons.Default.Refresh, contentDescription = "Refresh from Linear")
            }
        }

        Text(
            text = description,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        if (loading) LinearProgressIndicator(modifier = Modifier.fillMaxWidth())

        loadError?.let { err ->
            Surface(
                modifier = Modifier.fillMaxWidth(),
                color = MaterialTheme.colorScheme.errorContainer,
                contentColor = MaterialTheme.colorScheme.onErrorContainer,
                shape = MaterialTheme.shapes.small,
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = err,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.weight(1f),
                    )
                    PixelTextButton(onClick = onRefresh) { Text("Retry") }
                }
            }
        }

        ChipGroupSection(
            title = "Trigger labels",
            description = "Issues with any of these labels will be picked up.",
            error = errors.triggerLabels,
            names = labelNames,
            selected = triggerLabels,
            empty = if (labelNames.isEmpty() && !loading) {
                "No labels found — verify your Linear API key and refresh."
            } else {
                null
            },
            onToggle = onToggleTriggerLabel,
            chipLeading = null,
            modifier = Modifier.testTag("linear_filter_labels"),
        )

        ChipGroupSection(
            title = "Trigger states",
            description = "Issues in any of these workflow states will be picked up.",
            error = errors.triggerStates,
            names = stateNames,
            selected = triggerStates,
            empty = if (stateNames.isEmpty() && !loading) {
                "No workflow states found — verify your Linear API key and refresh."
            } else {
                null
            },
            onToggle = onToggleTriggerState,
            chipLeading = { name ->
                val type = stateByName[name]?.type.orEmpty()
                if (type.isNotBlank()) {
                    Text(text = stateTypeGlyph(type), style = MaterialTheme.typography.bodyMedium)
                }
            },
            modifier = Modifier.testTag("linear_filter_states"),
        )

        SingleSelectDropdown(
            label = "Active state (set on claim)",
            value = activeState,
            options = stateNames,
            error = errors.activeState,
            onChange = onActiveStateChange,
        )

        SingleSelectDropdown(
            label = "Done state (cleanup trigger)",
            value = doneState,
            options = stateNames,
            error = errors.doneState,
            onChange = onDoneStateChange,
        )

        SingleSelectDropdown(
            label = "Removal label (stripped on claim)",
            value = removalLabel,
            options = labelNames,
            error = errors.removalLabel,
            onChange = onRemovalLabelChange,
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ChipGroupSection(
    title: String,
    description: String,
    error: String?,
    names: List<String>,
    selected: Set<String>,
    empty: String?,
    onToggle: (String) -> Unit,
    chipLeading: (@Composable (String) -> Unit)?,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(text = title, style = MaterialTheme.typography.labelLarge)
        Text(
            text = description,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (empty != null) {
            Text(
                text = empty,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        } else {
            FlowRow(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                names.forEach { name ->
                    FilterChip(
                        selected = name in selected,
                        onClick = { onToggle(name) },
                        label = { Text(name) },
                        leadingIcon = chipLeading?.let { lead -> { lead(name) } },
                        elevation = FilterChipDefaults.filterChipElevation(),
                    )
                }
            }
        }
        if (error != null) {
            Text(
                text = error,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SingleSelectDropdown(
    label: String,
    value: String,
    options: List<String>,
    error: String?,
    onChange: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { if (options.isNotEmpty()) expanded = !expanded },
    ) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = value,
            onValueChange = { /* read-only — selection-only field */ },
            readOnly = true,
            label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            isError = error != null,
            supportingText = { error?.let { Text(it) } },
            modifier = Modifier
                .fillMaxWidth()
                .menuAnchor(),
        )
        ExposedDropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            if (options.isEmpty()) {
                DropdownMenuItem(
                    text = { Text("No options — refresh from Linear") },
                    onClick = { expanded = false },
                    enabled = false,
                )
            } else {
                options.forEach { option ->
                    DropdownMenuItem(
                        text = { Text(option) },
                        onClick = {
                            onChange(option)
                            expanded = false
                        },
                    )
                }
            }
        }
    }
}

/** Map a Linear state ``type`` to a single-character glyph. The glyphs mirror
 *  the icons Linear's web app uses, but as Unicode characters so the chip
 *  can render the leading slot without pulling in an icon pack. */
private fun stateTypeGlyph(type: String): String = when (type) {
    "backlog" -> "◴"
    "unstarted" -> "○"
    "started" -> "◐"
    "completed" -> "●"
    "canceled" -> "⊘"
    "triage" -> "△"
    else -> "·"
}

@Preview(name = "AutoScanLinearFilterEditor light", showBackground = true)
@Preview(
    name = "AutoScanLinearFilterEditor dark",
    showBackground = true,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun AutoScanLinearFilterEditorPreview() {
    MobaideTheme {
        AutoScanLinearFilterEditor(
            availableLabels = listOf(LinearLabelDto("AutoPipeline"), LinearLabelDto("Bug")),
            availableStates = listOf(
                LinearStateDto("Todo", "unstarted"),
                LinearStateDto("In Progress", "started"),
                LinearStateDto("Done", "completed"),
            ),
            triggerLabels = setOf("AutoPipeline"),
            triggerStates = emptySet(),
            activeState = "In Progress",
            doneState = "",
            removalLabel = "",
            loading = false,
            loadError = null,
            errors = LinearFilterErrors(triggerStates = "Select at least one trigger state"),
            onToggleTriggerLabel = {},
            onToggleTriggerState = {},
            onActiveStateChange = {},
            onDoneStateChange = {},
            onRemovalLabelChange = {},
            onRefresh = {},
            modifier = Modifier.padding(16.dp),
        )
    }
}
