package com.mobaide.app.ui.agents

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
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
import androidx.compose.ui.unit.dp

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

@Composable
fun DeleteAgentDialog(
    agentName: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Delete Agent") },
        text = {
            Text(
                "Are you sure you want to delete \"$agentName\"? " +
                    "This will permanently remove the agent and all associated data.",
            )
        },
        confirmButton = {
            PixelTextButton(onClick = onConfirm) {
                Text("Delete", color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}
