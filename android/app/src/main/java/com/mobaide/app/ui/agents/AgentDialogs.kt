package com.mobaide.app.ui.agents

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.common.copyToClipboard

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
            AlertDialog(
                onDismissRequest = onDismiss,
                title = { Text("Add Agent") },
                text = {
                    OutlinedTextField(
                        value = name,
                        onValueChange = { name = it },
                        label = { Text("Agent Name") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                },
                confirmButton = {
                    TextButton(
                        onClick = { onCreateAgent(name.trim()) },
                        enabled = name.isNotBlank(),
                    ) {
                        Text("Create")
                    }
                },
                dismissButton = {
                    TextButton(onClick = onDismiss) { Text("Cancel") }
                },
            )
        }
        is AddAgentDialogState.Loading -> {
            AlertDialog(
                onDismissRequest = {},
                title = { Text("Creating Agent...") },
                text = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(modifier = Modifier.size(24.dp))
                        Spacer(modifier = Modifier.width(16.dp))
                        Text("Please wait")
                    }
                },
                confirmButton = {},
            )
        }
        is AddAgentDialogState.ShowingCredentials -> {
            CredentialsDialog(
                title = "Agent Created",
                message = "Save these credentials now. The token will not be shown again.",
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
            AlertDialog(
                onDismissRequest = onDismiss,
                title = { Text("Re-register Agent") },
                text = {
                    Text(
                        "This will generate new credentials for \"${state.agentName}\" " +
                            "and invalidate the existing token immediately. Continue?",
                    )
                },
                confirmButton = {
                    TextButton(onClick = { onConfirm(state.agentId) }) {
                        Text("Re-register")
                    }
                },
                dismissButton = {
                    TextButton(onClick = onDismiss) { Text("Cancel") }
                },
            )
        }
        is ReRegisterDialogState.Loading -> {
            AlertDialog(
                onDismissRequest = {},
                title = { Text("Re-registering...") },
                text = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(modifier = Modifier.size(24.dp))
                        Spacer(modifier = Modifier.width(16.dp))
                        Text("Please wait")
                    }
                },
                confirmButton = {},
            )
        }
        is ReRegisterDialogState.ShowingCredentials -> {
            CredentialsDialog(
                title = "New Credentials",
                message = "Save these credentials now. The token will not be shown again.",
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
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Delete Agent") },
        text = {
            Text(
                "Are you sure you want to delete \"$agentName\"? " +
                    "This will permanently remove the agent and all associated data.",
            )
        },
        confirmButton = {
            TextButton(onClick = onConfirm) {
                Text("Delete", color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

@Composable
private fun CredentialsDialog(
    title: String,
    message: String,
    agentId: String,
    token: String,
    onDismiss: () -> Unit,
) {
    val context = LocalContext.current
    AlertDialog(
        onDismissRequest = {}, // prevent accidental dismiss
        title = { Text(title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    text = message,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error,
                    fontWeight = FontWeight.Medium,
                )

                Text("Agent ID", style = MaterialTheme.typography.labelMedium)
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        text = agentId,
                        style = MaterialTheme.typography.bodyMedium,
                        fontFamily = FontFamily.Monospace,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    IconButton(onClick = {
                        context.copyToClipboard("Agent ID", agentId)
                    }) {
                        Icon(
                            Icons.Default.ContentCopy,
                            contentDescription = "Copy Agent ID",
                            modifier = Modifier.size(18.dp),
                        )
                    }
                }

                Text("Token", style = MaterialTheme.typography.labelMedium)
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        text = token,
                        style = MaterialTheme.typography.bodyMedium,
                        fontFamily = FontFamily.Monospace,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    IconButton(onClick = {
                        context.copyToClipboard("Agent Token", token, isSensitive = true)
                    }) {
                        Icon(
                            Icons.Default.ContentCopy,
                            contentDescription = "Copy Token",
                            modifier = Modifier.size(18.dp),
                        )
                    }
                }

                Spacer(modifier = Modifier.height(4.dp))
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("Done") }
        },
    )
}
