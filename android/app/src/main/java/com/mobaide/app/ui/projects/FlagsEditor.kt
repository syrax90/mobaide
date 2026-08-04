package com.mobaide.app.ui.projects

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp

@Composable
fun FlagsEditor(
    flags: List<FlagEntry>,
    keyErrors: Map<Int, String>,
    valueErrors: Map<Int, String>,
    onUpdate: (Int, FlagEntry) -> Unit,
    onRemove: (Int) -> Unit,
    onAdd: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            text = "Default flags",
            style = MaterialTheme.typography.titleSmall,
        )
        Text(
            text = "Key/value pairs passed when launching Claude for this project. Types round-trip as typed JSON.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        flags.forEachIndexed { index, entry ->
            FlagRow(
                entry = entry,
                keyError = keyErrors[index],
                valueError = valueErrors[index],
                onUpdate = { onUpdate(index, it) },
                onRemove = { onRemove(index) },
            )
        }
        OutlinedButton(
            onClick = onAdd,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Icon(Icons.Default.Add, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text("Add flag")
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun FlagRow(
    entry: FlagEntry,
    keyError: String?,
    valueError: String?,
    onUpdate: (FlagEntry) -> Unit,
    onRemove: () -> Unit,
) {
    Column(
        modifier = Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                value = entry.key,
                onValueChange = { onUpdate(entry.copy(key = it)) },
                label = { Text("Key") },
                isError = keyError != null,
                supportingText = { keyError?.let { Text(it) } },
                singleLine = true,
                // The key becomes the flag name (`add-dir`, `model`) on the
                // agent's command line — exactly the short hyphenated token an
                // IME likes to "correct".
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.None,
                    autoCorrectEnabled = false,
                ),
                modifier = Modifier.weight(1f),
            )
            TypeDropdown(
                type = entry.type,
                onChange = { newType ->
                    val coerced = when (newType) {
                        FlagType.BOOLEAN -> entry.copy(
                            type = newType,
                            textValue = if (entry.textValue == "true") "true" else "false",
                        )
                        FlagType.NUMBER -> entry.copy(type = newType)
                        FlagType.STRING -> entry.copy(type = newType)
                    }
                    onUpdate(coerced)
                },
                modifier = Modifier.width(120.dp),
            )
            IconButton(onClick = onRemove) {
                Icon(Icons.Default.Close, contentDescription = "Remove flag")
            }
        }
        when (entry.type) {
            FlagType.BOOLEAN -> {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.padding(start = 4.dp),
                ) {
                    Switch(
                        checked = entry.textValue == "true",
                        onCheckedChange = { checked ->
                            onUpdate(entry.copy(textValue = if (checked) "true" else "false"))
                        },
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(if (entry.textValue == "true") "true" else "false")
                }
            }
            FlagType.NUMBER -> OutlinedTextField(
                value = entry.textValue,
                onValueChange = { onUpdate(entry.copy(textValue = it)) },
                label = { Text("Value (number)") },
                isError = valueError != null,
                supportingText = { valueError?.let { Text(it) } },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                modifier = Modifier.fillMaxWidth(),
            )
            FlagType.STRING -> OutlinedTextField(
                value = entry.textValue,
                onValueChange = { onUpdate(entry.copy(textValue = it)) },
                label = { Text("Value") },
                isError = valueError != null,
                supportingText = { valueError?.let { Text(it) } },
                singleLine = true,
                // Flag values reach the agent's command line verbatim — paths,
                // regexes and URLs must survive the keyboard untouched.
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.None,
                    autoCorrectEnabled = false,
                ),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        Spacer(Modifier.height(4.dp))
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TypeDropdown(
    type: FlagType,
    onChange: (FlagType) -> Unit,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = !expanded },
        modifier = modifier,
    ) {
        OutlinedTextField(
            value = type.label,
            onValueChange = {},
            readOnly = true,
            label = { Text("Type") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            FlagType.values().forEach { option ->
                DropdownMenuItem(
                    text = { Text(option.label) },
                    onClick = {
                        onChange(option)
                        expanded = false
                    },
                )
            }
        }
    }
}

private val FlagType.label: String
    get() = when (this) {
        FlagType.STRING -> "String"
        FlagType.BOOLEAN -> "Boolean"
        FlagType.NUMBER -> "Number"
    }
