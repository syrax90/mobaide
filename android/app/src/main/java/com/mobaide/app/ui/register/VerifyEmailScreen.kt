package com.mobaide.app.ui.register

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.PixelScaffold
import com.mobaide.app.ui.common.pixel.PixelSnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.ui.common.CodeInputField
import com.mobaide.app.ui.util.maskEmail

@Composable
fun VerifyEmailScreen(
    email: String,
    viewModel: VerifyEmailViewModel,
    onVerificationSuccess: () -> Unit,
    onChangeEmail: () -> Unit,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(email) {
        viewModel.setEmail(email)
    }

    LaunchedEffect(Unit) {
        viewModel.navEvents.collect { event ->
            when (event) {
                VerifyEmailNavEvent.Success -> onVerificationSuccess()
            }
        }
    }

    LaunchedEffect(Unit) {
        viewModel.errorEvents.collect { snackbarHostState.showSnackbar(it) }
    }

    PixelScaffold(
        snackbarHost = { PixelSnackbarHost(snackbarHostState) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 32.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = "Check your email",
                style = MaterialTheme.typography.headlineLarge,
            )

            Spacer(modifier = Modifier.height(8.dp))

            Text(
                text = "We sent a 6-digit code to ${maskEmail(email)}.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )

            Spacer(modifier = Modifier.height(32.dp))

            CodeInputField(
                value = uiState.code,
                onValueChange = viewModel::updateCode,
                enabled = !uiState.isLoading,
                isError = uiState.error != null,
                imeAction = ImeAction.Done,
                onImeAction = viewModel::submit,
                contentDescription = "Verification code",
                modifier = Modifier.fillMaxWidth(),
            )

            Spacer(modifier = Modifier.height(24.dp))

            PixelButton(
                style = PixelButtonStyle.Primary,
                onClick = viewModel::submit,
                enabled = !uiState.isLoading && uiState.code.length == 6,
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (uiState.isLoading) {
                    PixelBlinkCursor(
                        height = 20.dp,
                        color = LocalContentColor.current,
                    )
                } else {
                    Text("Verify")
                }
            }

            Spacer(modifier = Modifier.height(24.dp))

            val resendLabel = when {
                uiState.isResending -> "Sending…"
                uiState.cooldownSeconds > 0 -> "Resend code (${uiState.cooldownSeconds}s)"
                else -> "Resend code"
            }
            PixelTextButton(
                onClick = viewModel::resend,
                enabled = !uiState.isResending && uiState.cooldownSeconds == 0,
            ) {
                Text(resendLabel)
            }

            PixelTextButton(onClick = onChangeEmail) {
                Text("Use a different email")
            }
        }
    }
}

