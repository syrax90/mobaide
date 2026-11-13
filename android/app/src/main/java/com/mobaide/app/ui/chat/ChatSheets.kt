package com.mobaide.app.ui.chat

import android.content.res.Configuration
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.chat.ChatAgentInfo
import com.mobaide.app.data.chat.ChatConfigOption
import com.mobaide.app.data.chat.ChatConfigValue
import com.mobaide.app.data.chat.ChatUsage
import com.mobaide.app.ui.common.CopyableCodeBlock
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import com.mobaide.app.ui.common.pixel.PixelRadio
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.common.pixel.pixelSwitchColors
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel

/** The values of one select option, grouped by `group`, the current one marked. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatConfigPickerSheet(
    option: ChatConfigOption,
    onPick: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    PixelBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        Column(modifier = Modifier.padding(horizontal = 16.dp)) {
            Text(text = option.name, style = MaterialTheme.typography.titleMedium)
            option.description?.takeIf { it.isNotBlank() }?.let {
                Text(text = it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(modifier = Modifier.height(8.dp))
            val groups = remember(option.options) { option.options.groupBy { it.group } }
            LazyColumn(
                modifier = Modifier.heightIn(max = 520.dp),
                contentPadding = PaddingValues(bottom = 24.dp),
            ) {
                groups.forEach { (group, values) ->
                    if (group != null) {
                        item(key = "group-$group") {
                            Text(
                                text = group.chromeCaps(),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(top = 12.dp, bottom = 4.dp),
                            )
                        }
                    }
                    items(values, key = { "value-${it.value}" }) { value ->
                        ConfigValueRow(
                            value = value,
                            selected = value.value == option.selectValue,
                            onClick = { onPick(value.value) },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ConfigValueRow(value: ChatConfigValue, selected: Boolean, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .selectable(selected = selected, role = Role.RadioButton, onClick = onClick)
            .heightIn(min = 52.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PixelRadio(selected = selected, onClick = null)
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = value.name,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            )
            value.description?.takeIf { it.isNotBlank() }?.let {
                Text(text = it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/** Every setting the chips row does not show: selects as pickers, booleans as switches. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatSettingsSheet(
    options: List<ChatConfigOption>,
    enabled: Boolean,
    onSelect: (ChatConfigOption, String) -> Unit,
    onToggle: (ChatConfigOption, Boolean) -> Unit,
    onDismiss: () -> Unit,
) {
    PixelBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        Column(
            modifier = Modifier
                .padding(horizontal = 16.dp)
                .padding(bottom = 24.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(text = "Settings", style = MaterialTheme.typography.titleMedium)
            Spacer(modifier = Modifier.height(4.dp))
            options.forEach { option ->
                if (option.isBoolean) {
                    BooleanSettingRow(option, enabled, onToggle)
                } else {
                    SelectSettingRow(option, enabled, onSelect)
                }
            }
        }
    }
}

@Composable
private fun BooleanSettingRow(
    option: ChatConfigOption,
    enabled: Boolean,
    onToggle: (ChatConfigOption, Boolean) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .toggleable(
                value = option.boolValue,
                enabled = enabled,
                role = Role.Switch,
                onValueChange = { onToggle(option, it) },
            )
            .heightIn(min = 56.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(text = option.name, style = MaterialTheme.typography.bodyMedium)
            option.description?.takeIf { it.isNotBlank() }?.let {
                Text(text = it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Switch(checked = option.boolValue, onCheckedChange = null, enabled = enabled, colors = pixelSwitchColors())
    }
}

@Composable
private fun SelectSettingRow(
    option: ChatConfigOption,
    enabled: Boolean,
    onSelect: (ChatConfigOption, String) -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    Box {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable(enabled = enabled, role = Role.DropdownList) { open = true }
                .heightIn(min = 56.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(text = option.name, style = MaterialTheme.typography.bodyMedium)
                option.description?.takeIf { it.isNotBlank() }?.let {
                    Text(
                        text = it,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Text(
                text = option.currentName().orEmpty(),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.primary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Icon(Icons.Default.ArrowDropDown, contentDescription = null)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            option.options.forEach { value ->
                DropdownMenuItem(
                    text = {
                        Column {
                            Text(value.name)
                            value.description?.takeIf { it.isNotBlank() }?.let {
                                Text(
                                    text = it,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    },
                    leadingIcon = { PixelRadio(selected = value.value == option.selectValue, onClick = null) },
                    onClick = {
                        open = false
                        onSelect(option, value.value)
                    },
                )
            }
        }
    }
}

@Composable
fun ChatSessionInfoDialog(
    agent: ChatAgentInfo?,
    cwd: String?,
    acpSessionId: String?,
    onDismiss: () -> Unit,
) {
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Session info") },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                val agentLine = listOfNotNull(agent?.title ?: agent?.name, agent?.version).joinToString(" ")
                if (agentLine.isNotBlank()) CopyableCodeBlock(label = "Agent", code = agentLine, singleLine = false)
                if (!cwd.isNullOrBlank()) CopyableCodeBlock(label = "Folder", code = cwd, singleLine = false)
                if (!acpSessionId.isNullOrBlank()) CopyableCodeBlock(label = "ACP session ID", code = acpSessionId, singleLine = false)
                if (agentLine.isBlank() && cwd.isNullOrBlank() && acpSessionId.isNullOrBlank()) {
                    Text("The agent has not reported its details yet.")
                }
            }
        },
        confirmButton = { PixelTextButton(onClick = onDismiss) { Text("Close") } },
    )
}

/** Context use as a small bar; tapping it shows the numbers and the cost. */
@Composable
fun ChatUsageIndicator(usage: ChatUsage, modifier: Modifier = Modifier) {
    var open by remember { mutableStateOf(false) }
    val fraction = usageFraction(usage)
    val tone = when {
        fraction >= 0.95f -> MaterialTheme.pixel.danger
        fraction >= 0.8f -> MaterialTheme.pixel.warning
        else -> MaterialTheme.colorScheme.primary
    }
    Box(modifier = modifier) {
        Box(
            modifier = Modifier
                .minimumInteractiveComponentSize()
                .semantics { contentDescription = "Context used: ${usagePercent(usage)} percent" }
                .clickable(role = Role.Button) { open = true },
            contentAlignment = Alignment.Center,
        ) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    modifier = Modifier
                        .size(width = 26.dp, height = 10.dp)
                        .pixelFrame(MaterialTheme.colorScheme.outline, width = 2.dp)
                        .padding(2.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .fillMaxHeight()
                            .fillMaxWidth(fraction.coerceAtLeast(0.04f))
                            .background(tone),
                    )
                }
                Text(
                    text = "${usagePercent(usage)}%",
                    style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.clearAndSetSemantics { },
                )
            }
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
                Text(text = formatUsage(usage), style = MaterialTheme.typography.bodyMedium)
                usage.cost?.let {
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = formatCost(it),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Preview(name = "Usage light", showBackground = true)
@Preview(name = "Usage dark", showBackground = true, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
private fun ChatUsageIndicatorPreview() {
    MobaideTheme {
        Row(modifier = Modifier.padding(8.dp)) {
            ChatUsageIndicator(ChatUsage(53_000, 200_000, null))
            Spacer(modifier = Modifier.width(8.dp))
            ChatUsageIndicator(ChatUsage(170_000, 200_000, null))
            Spacer(modifier = Modifier.width(8.dp))
            ChatUsageIndicator(ChatUsage(195_000, 200_000, null))
        }
    }
}
