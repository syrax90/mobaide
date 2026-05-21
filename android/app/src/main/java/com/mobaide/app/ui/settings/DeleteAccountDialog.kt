package com.mobaide.app.ui.settings

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.auth.GoogleAuthClient
import kotlinx.coroutines.launch

/**
 * Three-step destructive dialog: Explain → ReAuth → ConfirmEmail.
 *
 * On success, [onScheduled] fires once the server has accepted the request
 * and the local WS-4010 path is about to wipe the session. Callers usually
 * just route to the login screen and let [com.mobaide.app.data.auth.SessionManager]
 * clear state — no additional plumbing is needed here.
 */
@Composable
fun DeleteAccountDialog(
    state: DeleteAccountDialogState,
    viewModel: SettingsViewModel,
    onDismiss: () -> Unit,
    onScheduled: () -> Unit,
) {
    if (state is DeleteAccountDialogState.Hidden) return

    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()
    val googleAuthClient = remember(context) { GoogleAuthClient(context) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                text = "Delete account",
                color = MaterialTheme.colorScheme.error,
                fontWeight = FontWeight.SemiBold,
            )
        },
        text = {
            when (state) {
                is DeleteAccountDialogState.Hidden -> Unit
                is DeleteAccountDialogState.Explain -> ExplainBody(state)
                is DeleteAccountDialogState.ReAuthPassword -> ReAuthPasswordBody(
                    state = state,
                    onPasswordChange = viewModel::onPasswordChange,
                )
                is DeleteAccountDialogState.ReAuthGoogle -> ReAuthGoogleBody(state)
                is DeleteAccountDialogState.ConfirmEmail -> ConfirmEmailBody(
                    state = state,
                    onTypedEmailChange = viewModel::onTypedEmailChange,
                )
            }
        },
        confirmButton = {
            when (state) {
                is DeleteAccountDialogState.Hidden -> Unit
                is DeleteAccountDialogState.Explain -> Row {
                    TextButton(onClick = { viewModel.chooseReAuthMethod(useGoogle = false) }) {
                        Text("Continue with password")
                    }
                    Spacer(modifier = Modifier.height(4.dp))
                    TextButton(onClick = { viewModel.chooseReAuthMethod(useGoogle = true) }) {
                        Text("Continue with Google")
                    }
                }
                is DeleteAccountDialogState.ReAuthPassword -> Button(
                    onClick = viewModel::confirmPasswordReauth,
                    enabled = !state.submitting,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.error,
                    ),
                ) {
                    Text("Next")
                }
                is DeleteAccountDialogState.ReAuthGoogle -> Button(
                    onClick = {
                        viewModel.markGoogleReauthSubmitting()
                        coroutineScope.launch {
                            val result = googleAuthClient.signIn()
                            viewModel.onGoogleAuthResult(result)
                        }
                    },
                    enabled = !state.submitting,
                ) {
                    if (state.submitting) {
                        CircularProgressIndicator(
                            modifier = Modifier.height(18.dp),
                            color = MaterialTheme.colorScheme.onPrimary,
                            strokeWidth = 2.dp,
                        )
                    } else {
                        Text("Re-verify with Google")
                    }
                }
                is DeleteAccountDialogState.ConfirmEmail -> Button(
                    onClick = { viewModel.submitDelete(onScheduled) },
                    enabled = !state.submitting && state.typedEmail == state.email,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.error,
                    ),
                ) {
                    if (state.submitting) {
                        CircularProgressIndicator(
                            modifier = Modifier.height(18.dp),
                            color = MaterialTheme.colorScheme.onPrimary,
                            strokeWidth = 2.dp,
                        )
                    } else {
                        Text("Schedule deletion")
                    }
                }
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

@Composable
private fun ExplainBody(state: DeleteAccountDialogState.Explain) {
    Column {
        Text(
            text = "You're about to permanently schedule the deletion of:",
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = state.email,
            style = MaterialTheme.typography.bodyLarge,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(12.dp))
        Text(
            text =
                "Your account will be soft-deleted for 14 days. You can sign back " +
                    "in within that window to cancel; after that, your data — including " +
                    "every agent, pairing, and prompt template — is permanently removed.",
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = "You'll need to re-verify your identity in the next step.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun ReAuthPasswordBody(
    state: DeleteAccountDialogState.ReAuthPassword,
    onPasswordChange: (String) -> Unit,
) {
    Column {
        Text("Enter your current password to confirm:")
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = state.password,
            onValueChange = onPasswordChange,
            label = { Text("Password") },
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            singleLine = true,
            isError = state.errorMessage != null,
            modifier = Modifier.fillMaxWidth(),
        )
        if (state.errorMessage != null) {
            Spacer(Modifier.height(4.dp))
            Text(
                text = state.errorMessage,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun ReAuthGoogleBody(state: DeleteAccountDialogState.ReAuthGoogle) {
    Column {
        Text(
            text =
                "Tap below to re-verify with Google. A fresh sign-in is required — your " +
                    "existing session alone is not enough to confirm this destructive action.",
            style = MaterialTheme.typography.bodyMedium,
        )
        if (state.errorMessage != null) {
            Spacer(Modifier.height(8.dp))
            Text(
                text = state.errorMessage,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun ConfirmEmailBody(
    state: DeleteAccountDialogState.ConfirmEmail,
    onTypedEmailChange: (String) -> Unit,
) {
    Column {
        Text("Type your email to confirm:")
        Spacer(Modifier.height(4.dp))
        Text(
            text = state.email,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = state.typedEmail,
            onValueChange = onTypedEmailChange,
            label = { Text("Email") },
            singleLine = true,
            isError = state.errorMessage != null,
            modifier = Modifier.fillMaxWidth(),
        )
        if (state.errorMessage != null) {
            Spacer(Modifier.height(4.dp))
            Text(
                text = state.errorMessage,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

