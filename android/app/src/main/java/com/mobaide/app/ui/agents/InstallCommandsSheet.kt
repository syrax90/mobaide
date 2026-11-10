package com.mobaide.app.ui.agents

import android.os.Build
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import com.mobaide.app.ui.theme.ChromeFontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.common.copyToClipboard

internal const val INSTALL_COMMAND_LINUX_MACOS =
    "curl -fsSL https://get.mobaide.com/install.sh | bash"
internal const val INSTALL_COMMAND_WINDOWS =
    "irm https://get.mobaide.com/install.ps1 | iex"

/**
 * Bottom sheet that surfaces the host install one-liners for `mobaide-agent`.
 * Renders both Linux/macOS and Windows commands simultaneously — the user
 * is installing on a *different* machine than the phone, so auto-detection
 * would actively mislead.
 *
 * `internal` so [AgentListScreenTest] can compose it directly without
 * driving it through [AgentListViewModel].
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun InstallCommandsSheet(onDismiss: () -> Unit) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    PixelBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(
                text = "Install mobaide-agent on a host",
                style = MaterialTheme.typography.titleLarge,
            )
            Text(
                text = "Run one of these commands on the machine you want to use as a " +
                    "host. After install completes, the agent will display a pairing QR " +
                    "— scan it from this screen.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            InstallCommandCard(
                osLabel = "Linux / macOS",
                command = INSTALL_COMMAND_LINUX_MACOS,
            )
            InstallCommandCard(
                osLabel = "Windows",
                command = INSTALL_COMMAND_WINDOWS,
            )
            Spacer(modifier = Modifier.size(8.dp))
        }
    }
}

/**
 * `internal` (vs the file's usual `private`) so [AgentListScreenTest] can
 * compose the card directly without going through [ModalBottomSheet] —
 * Robolectric's input dispatcher does not deliver synthetic click events
 * to nodes rendered inside the sheet's popup window.
 */
@Composable
internal fun InstallCommandCard(osLabel: String, command: String) {
    val context = LocalContext.current
    val haptic = LocalHapticFeedback.current
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHighest)
            .pixelFrame(MaterialTheme.colorScheme.outlineVariant)
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                Icons.Default.Terminal,
                contentDescription = null,
                modifier = Modifier.size(20.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = osLabel,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Medium,
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // The weight sits on a Box: SelectionContainer does not pass it on
            // to the Row, so a long command hid the copy button.
            Box(modifier = Modifier.weight(1f)) {
                SelectionContainer {
                    Text(
                        text = command,
                        style = MaterialTheme.typography.bodyMedium,
                        fontFamily = ChromeFontFamily,
                        maxLines = 1,
                        modifier = Modifier.horizontalScroll(rememberScrollState()),
                    )
                }
            }
            IconButton(onClick = {
                // Android 13+ shows a system clipboard chip; stacking an
                // in-app Toast would visually duplicate that confirmation.
                context.copyToClipboard(
                    label = "Mobaide install command",
                    text = command,
                    isSensitive = false,
                )
                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
                    Toast.makeText(context, "Command copied", Toast.LENGTH_SHORT).show()
                }
            }) {
                Icon(
                    Icons.Default.ContentCopy,
                    contentDescription = "Copy $osLabel install command",
                    modifier = Modifier.size(20.dp),
                )
            }
        }
    }
}
