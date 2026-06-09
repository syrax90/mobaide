package com.mobaide.app.ui.settings

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.BuildConfig
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_MAX
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_MIN
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_STEP
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.ui.util.openUrlInCustomTab

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: SettingsViewModel,
    onBack: () -> Unit,
    onLogout: () -> Unit,
    onManageConnections: () -> Unit = {},
    onManageTemplates: () -> Unit = {},
    onManageDevices: () -> Unit = {},
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val context = LocalContext.current

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings") },
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
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState()),
        ) {
            ListItem(
                headlineContent = { Text("Logged in as") },
                supportingContent = { Text(uiState.userEmail) },
            )
            HorizontalDivider()

            Spacer(modifier = Modifier.height(8.dp))

            Button(
                onClick = onManageConnections,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                Text("My Agents")
            }

            Spacer(modifier = Modifier.height(8.dp))

            Button(
                onClick = onManageTemplates,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                Text("Prompt templates")
            }

            Spacer(modifier = Modifier.height(8.dp))

            Button(
                onClick = onManageDevices,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                Text("My devices")
            }

            Spacer(modifier = Modifier.height(8.dp))

            OutlinedButton(
                onClick = { openUrlInCustomTab(context, BuildConfig.PRIVACY_POLICY_URL) },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                Text("Privacy policy")
            }

            Spacer(modifier = Modifier.height(16.dp))

            HorizontalDivider()

            TerminalDisplaySection(
                mode = uiState.terminalDisplayMode,
                staticColumns = uiState.terminalStaticColumns,
                onModeChange = viewModel::setTerminalDisplayMode,
                onColumnsChange = viewModel::setTerminalStaticColumns,
            )

            HorizontalDivider()

            Spacer(modifier = Modifier.height(16.dp))

            Button(
                onClick = {
                    viewModel.logout()
                    onLogout()
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.error,
                ),
            ) {
                Text("Logout")
            }

            Spacer(modifier = Modifier.height(24.dp))

            HorizontalDivider()
            DangerZoneSection(onDeleteAccount = viewModel::startDeleteAccount)

            Spacer(modifier = Modifier.height(24.dp))

            Text(
                text = "Mobaide v${BuildConfig.VERSION_NAME}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(16.dp),
            )
        }

        if (uiState.deleteDialog !is DeleteAccountDialogState.Hidden) {
            DeleteAccountDialog(
                state = uiState.deleteDialog,
                viewModel = viewModel,
                onDismiss = viewModel::dismissDeleteDialog,
                onSubmitted = { _ -> onLogout() },
            )
        }
    }
}

@Composable
private fun DangerZoneSection(onDeleteAccount: () -> Unit) {
    Column(modifier = Modifier.padding(16.dp)) {
        Text(
            text = "Danger zone",
            style = MaterialTheme.typography.titleSmall,
            color = MaterialTheme.colorScheme.error,
        )
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = "Permanently delete your account, all owned agents, and every saved template.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(modifier = Modifier.height(12.dp))
        OutlinedButton(
            onClick = onDeleteAccount,
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.outlinedButtonColors(
                contentColor = MaterialTheme.colorScheme.error,
            ),
            border = BorderStroke(1.dp, MaterialTheme.colorScheme.error),
        ) {
            Text("Delete account")
        }
    }
}

@Composable
private fun TerminalDisplaySection(
    mode: TerminalDisplayMode,
    staticColumns: Int,
    onModeChange: (TerminalDisplayMode) -> Unit,
    onColumnsChange: (Int) -> Unit,
) {
    Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
        Text(
            text = "Terminal display",
            style = MaterialTheme.typography.titleMedium,
        )
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = "Full Screen reflows columns to the viewport. Static keeps a fixed column count and pans horizontally.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(modifier = Modifier.height(8.dp))

        Column(modifier = Modifier.selectableGroup()) {
            DisplayModeRow(
                label = "Full Screen",
                selected = mode == TerminalDisplayMode.Full,
                onSelect = { onModeChange(TerminalDisplayMode.Full) },
            )
            DisplayModeRow(
                label = "Static (fixed columns)",
                selected = mode == TerminalDisplayMode.Static,
                onSelect = { onModeChange(TerminalDisplayMode.Static) },
            )
        }

        Spacer(modifier = Modifier.height(8.dp))

        ColumnsStepper(
            value = staticColumns,
            enabled = mode == TerminalDisplayMode.Static,
            onValueChange = onColumnsChange,
        )
    }
}

@Composable
private fun DisplayModeRow(
    label: String,
    selected: Boolean,
    onSelect: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .selectable(
                selected = selected,
                onClick = onSelect,
                role = Role.RadioButton,
            )
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = null)
        Spacer(modifier = Modifier.width(8.dp))
        Text(text = label, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun ColumnsStepper(
    value: Int,
    enabled: Boolean,
    onValueChange: (Int) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = "Columns",
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.weight(1f),
            color = if (enabled) {
                MaterialTheme.colorScheme.onSurface
            } else {
                MaterialTheme.colorScheme.onSurfaceVariant
            },
        )
        IconButton(
            enabled = enabled && value > TERMINAL_STATIC_COLUMNS_MIN,
            onClick = { onValueChange(value - TERMINAL_STATIC_COLUMNS_STEP) },
        ) {
            Icon(Icons.Default.Remove, contentDescription = "Decrease columns")
        }
        Text(
            text = value.toString(),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier
                .width(48.dp)
                .padding(horizontal = 4.dp),
            color = if (enabled) {
                MaterialTheme.colorScheme.onSurface
            } else {
                MaterialTheme.colorScheme.onSurfaceVariant
            },
        )
        IconButton(
            enabled = enabled && value < TERMINAL_STATIC_COLUMNS_MAX,
            onClick = { onValueChange(value + TERMINAL_STATIC_COLUMNS_STEP) },
        ) {
            Icon(Icons.Default.Add, contentDescription = "Increase columns")
        }
    }
}
