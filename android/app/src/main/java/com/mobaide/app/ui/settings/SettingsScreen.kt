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
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.offset
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.Alignment
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.zIndex
import com.mobaide.app.data.local.ThemeMode
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel
import com.mobaide.app.ui.common.pixel.PixelRadio
import com.mobaide.app.ui.common.pixel.PixelScaffold
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTopBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.BuildConfig
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_MAX
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_MIN
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_STEP
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.ui.util.openUrlInCustomTab
import com.mobaide.app.ui.voice.VoiceSettingsSection
import com.mobaide.app.ui.voice.VoiceViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: SettingsViewModel,
    voiceViewModel: VoiceViewModel,
    onBack: () -> Unit,
    onLogout: () -> Unit,
    onManageConnections: () -> Unit = {},
    onManageTemplates: () -> Unit = {},
    onManageDevices: () -> Unit = {},
    onOpenSupport: () -> Unit = {},
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val voiceState by voiceViewModel.uiState.collectAsStateWithLifecycle()
    val context = LocalContext.current

    PixelScaffold(
        topBar = {
            PixelTopBar(
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
            HorizontalDivider(thickness = 2.dp)

            Spacer(modifier = Modifier.height(8.dp))

            PixelButton(
                onClick = onManageConnections,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                Text("My Agents")
            }

            Spacer(modifier = Modifier.height(8.dp))

            PixelButton(
                onClick = onManageTemplates,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                Text("Prompt templates")
            }

            Spacer(modifier = Modifier.height(8.dp))

            PixelButton(
                onClick = onManageDevices,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                Text("My devices")
            }

            Spacer(modifier = Modifier.height(8.dp))

            PixelButton(
                onClick = onOpenSupport,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                Text("Help & Support")
            }

            Spacer(modifier = Modifier.height(8.dp))

            PixelButton(
                onClick = { openUrlInCustomTab(context, BuildConfig.PRIVACY_POLICY_URL) },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                Text("Privacy policy")
            }

            Spacer(modifier = Modifier.height(16.dp))

            AppearanceSection(
                mode = uiState.themeMode,
                onModeChange = viewModel::setThemeMode,
            )

            HorizontalDivider(thickness = 2.dp)

            TerminalDisplaySection(
                mode = uiState.terminalDisplayMode,
                staticColumns = uiState.terminalStaticColumns,
                onModeChange = viewModel::setTerminalDisplayMode,
                onColumnsChange = viewModel::setTerminalStaticColumns,
            )

            HorizontalDivider(thickness = 2.dp)

            VoiceSettingsSection(
                state = voiceState,
                onEnable = voiceViewModel::install,
                onCancelInstall = voiceViewModel::cancelInstall,
                onRetry = voiceViewModel::install,
                onRemove = voiceViewModel::remove,
                onReinstall = voiceViewModel::reinstall,
                onShowMicChange = { show -> voiceViewModel.setMicHidden(!show) },
                onCleanUpChange = voiceViewModel::setCleanUp,
            )

            HorizontalDivider(thickness = 2.dp)

            Spacer(modifier = Modifier.height(16.dp))

            PixelButton(
                style = PixelButtonStyle.Danger,
                onClick = {
                    viewModel.logout()
                    onLogout()
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                Text("Logout")
            }

            Spacer(modifier = Modifier.height(24.dp))

            HorizontalDivider(thickness = 2.dp)
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

/**
 * The web Appearance card: a square Auto | Light | Dark segmented control.
 * Adjacent segments overlap by the 2dp ring width so they share one divider,
 * and the selected segment z-lifts so its brand-2 ring wins the overlap.
 */
@Composable
private fun AppearanceSection(
    mode: ThemeMode,
    onModeChange: (ThemeMode) -> Unit,
) {
    Column(modifier = Modifier.padding(16.dp)) {
        Text(
            text = "Appearance".chromeCaps(),
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(modifier = Modifier.height(8.dp))
        Row(modifier = Modifier.selectableGroup()) {
            ThemeMode.entries.forEachIndexed { index, candidate ->
                val selected = candidate == mode
                Box(
                    modifier = Modifier
                        .offset(x = (-2 * index).dp)
                        .zIndex(if (selected) 1f else 0f)
                        .background(
                            if (selected) MaterialTheme.colorScheme.surfaceContainerHighest
                            else Color.Transparent,
                        )
                        .pixelFrame(
                            if (selected) MaterialTheme.pixel.brand2
                            else MaterialTheme.colorScheme.outline,
                        )
                        .selectable(
                            selected = selected,
                            role = Role.RadioButton,
                            onClick = { onModeChange(candidate) },
                        )
                        .defaultMinSize(minWidth = 72.dp, minHeight = 48.dp)
                        .testTag("theme_mode_${candidate.name}"),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = candidate.name.chromeCaps(),
                        style = MaterialTheme.typography.labelLarge,
                        color = if (selected) {
                            MaterialTheme.colorScheme.onSurface
                        } else {
                            MaterialTheme.colorScheme.onSurfaceVariant
                        },
                    )
                }
            }
        }
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = "Auto follows the system theme.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
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
        PixelButton(
            onClick = onDeleteAccount,
            style = PixelButtonStyle.Danger,
            modifier = Modifier.fillMaxWidth(),
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
        PixelRadio(selected = selected, onClick = null)
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
