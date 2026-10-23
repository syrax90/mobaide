package com.mobaide.app.ui.agents

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.common.pixel.PixelCheckbox
import com.mobaide.app.ui.theme.MobaideTheme

sealed interface AddAgentDialogState {
    data object Hidden : AddAgentDialogState
    data object Input : AddAgentDialogState
    data object Loading : AddAgentDialogState
    data class ShowingCredentials(
        val agentId: String,
        val token: String,
    ) : AddAgentDialogState
}

sealed interface ReRegisterDialogState {
    data object Hidden : ReRegisterDialogState
    data class Confirming(val agentId: String, val agentName: String) : ReRegisterDialogState
    data class Loading(val agentId: String) : ReRegisterDialogState
    data class ShowingCredentials(
        val agentId: String,
        val token: String,
    ) : ReRegisterDialogState
}

@Composable
fun AddAgentDialog(
    state: AddAgentDialogState,
    onCreateAgent: (name: String) -> Unit,
    onDismiss: () -> Unit,
) {
    when (state) {
        is AddAgentDialogState.Hidden -> {}
        is AddAgentDialogState.Input -> {
            var name by rememberSaveable { mutableStateOf("") }
            PixelAlertDialog(
                onDismissRequest = onDismiss,
                title = { Text("Add Agent") },
                text = {
                    OutlinedTextField(
                        colors = PixelTextFieldDefaults.colors(),
                        value = name,
                        onValueChange = { name = it },
                        label = { Text("Agent Name") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                },
                confirmButton = {
                    PixelTextButton(
                        onClick = { onCreateAgent(name.trim()) },
                        enabled = name.isNotBlank(),
                    ) {
                        Text("Create")
                    }
                },
                dismissButton = {
                    PixelTextButton(onClick = onDismiss) { Text("Cancel") }
                },
            )
        }
        is AddAgentDialogState.Loading -> {
            PixelAlertDialog(
                onDismissRequest = {},
                title = { Text("Creating Agent...") },
                text = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        PixelBlinkCursor(height = 24.dp)
                        Spacer(modifier = Modifier.width(16.dp))
                        Text("Please wait")
                    }
                },
                confirmButton = {},
            )
        }
        is AddAgentDialogState.ShowingCredentials -> {
            AgentCredentialsSheet(
                title = "Agent Created",
                agentId = state.agentId,
                token = state.token,
                onDismiss = onDismiss,
            )
        }
    }
}

@Composable
fun ReRegisterDialog(
    state: ReRegisterDialogState,
    onConfirm: (agentId: String) -> Unit,
    onDismiss: () -> Unit,
) {
    when (state) {
        is ReRegisterDialogState.Hidden -> {}
        is ReRegisterDialogState.Confirming -> {
            PixelAlertDialog(
                onDismissRequest = onDismiss,
                title = { Text("Re-register Agent") },
                text = {
                    Text(
                        "This will generate new credentials for \"${state.agentName}\" " +
                            "and invalidate the existing token immediately. Continue?",
                    )
                },
                confirmButton = {
                    PixelTextButton(onClick = { onConfirm(state.agentId) }) {
                        Text("Re-register")
                    }
                },
                dismissButton = {
                    PixelTextButton(onClick = onDismiss) { Text("Cancel") }
                },
            )
        }
        is ReRegisterDialogState.Loading -> {
            PixelAlertDialog(
                onDismissRequest = {},
                title = { Text("Re-registering...") },
                text = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        PixelBlinkCursor(height = 24.dp)
                        Spacer(modifier = Modifier.width(16.dp))
                        Text("Please wait")
                    }
                },
                confirmButton = {},
            )
        }
        is ReRegisterDialogState.ShowingCredentials -> {
            AgentCredentialsSheet(
                title = "New Credentials",
                agentId = state.agentId,
                token = state.token,
                onDismiss = onDismiss,
            )
        }
    }
}

/**
 * Confirm removing an agent. When the agent's build supports it, offers
 * uninstalling it from its computer too — checked by default, because a
 * removed agent left on its host can never reconnect and only retries.
 */
@Composable
fun DeleteAgentDialog(
    agentName: String,
    online: Boolean,
    supportsUninstall: Boolean,
    onConfirm: (uninstall: Boolean) -> Unit,
    onDismiss: () -> Unit,
) {
    var uninstall by rememberSaveable(agentName) { mutableStateOf(supportsUninstall) }
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Delete Agent") },
        text = {
            Column {
                Text(
                    "Are you sure you want to delete \"$agentName\"? " +
                        "This will permanently remove the agent and all associated data.",
                )
                if (supportsUninstall) {
                    Spacer(Modifier.height(12.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        PixelCheckbox(
                            checked = uninstall,
                            onCheckedChange = { uninstall = it },
                            modifier = Modifier.testTag("delete_agent_uninstall_checkbox"),
                        )
                        Spacer(Modifier.width(4.dp))
                        Column {
                            Text(
                                "Also uninstall Mobaide from this computer",
                                style = MaterialTheme.typography.bodyMedium,
                            )
                            Text(
                                if (online) {
                                    "The agent stops and deletes itself right away."
                                } else {
                                    "The agent is offline, so this happens the next " +
                                        "time it connects (within 30 days)."
                                },
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        },
        confirmButton = {
            PixelTextButton(
                onClick = { onConfirm(supportsUninstall && uninstall) },
                modifier = Modifier.testTag("delete_agent_confirm"),
            ) {
                Text("Delete", color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

@Preview(name = "Delete agent dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun DeleteAgentDialogPreviewDark() {
    MobaideTheme(darkTheme = true) {
        DeleteAgentDialog("workstation", online = false, supportsUninstall = true, {}, {})
    }
}

@Preview(name = "Delete agent light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun DeleteAgentDialogPreviewLight() {
    MobaideTheme(darkTheme = false) {
        DeleteAgentDialog("workstation", online = true, supportsUninstall = true, {}, {})
    }
}
