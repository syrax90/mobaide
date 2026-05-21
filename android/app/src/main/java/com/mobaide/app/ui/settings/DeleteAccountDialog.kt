package com.mobaide.app.ui.settings

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.auth.GoogleAuthClient
import com.mobaide.app.data.repository.DeletionMode
import kotlinx.coroutines.launch

/**
 * Multi-step destructive dialog: Explain → ReAuth → ConfirmEmail.
 *
 * Explain step is where the user picks the deletion mode (Scheduled vs
 * Immediate) AND the re-auth method (Password vs Google). The chosen mode
 * threads through every later state so the final submit button can adapt
 * its label/colour and the API call carries the right value.
 *
 * On success, [onSubmitted] fires with the chosen mode once the server has
 * accepted the request and the local WS-4010 path is about to wipe the
 * session. Callers usually just route to the login screen and let
 * [com.mobaide.app.data.auth.SessionManager] clear state — no additional
 * plumbing is needed here.
 */
@Composable
fun DeleteAccountDialog(
    state: DeleteAccountDialogState,
    viewModel: SettingsViewModel,
    onDismiss: () -> Unit,
    onSubmitted: (DeletionMode) -> Unit,
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
                is DeleteAccountDialogState.Explain -> ExplainBody(
                    state = state,
                    onModeChange = viewModel::setDeletionMode,
                    onChoosePassword = { viewModel.chooseReAuthMethod(useGoogle = false) },
                    onChooseGoogle = { viewModel.chooseReAuthMethod(useGoogle = true) },
                )
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
                is DeleteAccountDialogState.Hidden,
                is DeleteAccountDialogState.Explain -> Unit
                is DeleteAccountDialogState.ReAuthPassword -> Button(
                    onClick = viewModel::confirmPasswordReauth,
                    enabled = !state.submitting,
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
                is DeleteAccountDialogState.ConfirmEmail -> {
                    val isImmediate = state.mode == DeletionMode.Immediate
                    val container = if (isImmediate) {
                        MaterialTheme.colorScheme.error
                    } else {
                        MaterialTheme.colorScheme.primary
                    }
                    val onContainer = if (isImmediate) {
                        MaterialTheme.colorScheme.onError
                    } else {
                        MaterialTheme.colorScheme.onPrimary
                    }
                    Button(
                        onClick = { viewModel.submitDelete(onSubmitted) },
                        enabled = !state.submitting && state.typedEmail == state.email,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = container,
                            contentColor = onContainer,
                        ),
                    ) {
                        if (state.submitting) {
                            CircularProgressIndicator(
                                modifier = Modifier.height(18.dp),
                                color = onContainer,
                                strokeWidth = 2.dp,
                            )
                        } else {
                            Text(submitLabelFor(state.mode))
                        }
                    }
                }
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

private fun submitLabelFor(mode: DeletionMode): String = when (mode) {
    DeletionMode.Scheduled -> "Schedule deletion"
    DeletionMode.Immediate -> "Delete immediately"
}

@Composable
private fun ExplainBody(
    state: DeleteAccountDialogState.Explain,
    onModeChange: (DeletionMode) -> Unit,
    onChoosePassword: () -> Unit,
    onChooseGoogle: () -> Unit,
) {
    Column {
        Text(
            text = "You're about to delete:",
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(4.dp))
        Text(
            text = state.email,
            style = MaterialTheme.typography.bodyLarge,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(12.dp))

        Text(
            text = "How should we process this?",
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(6.dp))
        ModeOption(
            selected = state.mode == DeletionMode.Scheduled,
            onClick = { onModeChange(DeletionMode.Scheduled) },
            title = "Schedule for 14 days (recommended)",
            subtitle = "You can cancel any time during the grace window — sign back in or use the email link.",
            danger = false,
        )
        Spacer(Modifier.height(6.dp))
        ModeOption(
            selected = state.mode == DeletionMode.Immediate,
            onClick = { onModeChange(DeletionMode.Immediate) },
            title = "Delete immediately",
            subtitle = "Irreversible. Every agent, pairing, and prompt template is purged now.",
            danger = true,
        )

        Spacer(Modifier.height(16.dp))
        Text(
            text = "Re-verify with",
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(6.dp))
        Button(
            onClick = onChoosePassword,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Continue with password")
        }
        Spacer(Modifier.height(8.dp))
        OutlinedButton(
            onClick = onChooseGoogle,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Continue with Google")
        }
    }
}

@Composable
private fun ModeOption(
    selected: Boolean,
    onClick: () -> Unit,
    title: String,
    subtitle: String,
    danger: Boolean,
) {
    val accent = if (danger) MaterialTheme.colorScheme.error
        else MaterialTheme.colorScheme.primary
    val borderColor = if (selected) accent else MaterialTheme.colorScheme.outlineVariant
    val textColor = if (selected && danger) accent
        else MaterialTheme.colorScheme.onSurface
    Surface(
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(if (selected) 2.dp else 1.dp, borderColor),
        color = MaterialTheme.colorScheme.surface,
        modifier = Modifier
            .fillMaxWidth()
            .selectable(
                selected = selected,
                onClick = onClick,
                role = Role.RadioButton,
            ),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.Top,
        ) {
            RadioButton(
                selected = selected,
                onClick = null,
                colors = RadioButtonDefaults.colors(selectedColor = accent),
            )
            Column(modifier = Modifier.padding(start = 8.dp)) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = textColor,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    text = subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
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
            text = "Tap below to re-verify with Google. A fresh sign-in is required — " +
                "your existing session alone is not enough to confirm this destructive action.",
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
        if (state.mode == DeletionMode.Immediate) {
            Text(
                text = "Final step — this will purge your account immediately.",
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.error,
            )
            Spacer(Modifier.height(8.dp))
        }
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

