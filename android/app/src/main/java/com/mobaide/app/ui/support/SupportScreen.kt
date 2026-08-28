package com.mobaide.app.ui.support

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import com.mobaide.app.ui.util.buildSupportEmailBody
import com.mobaide.app.ui.util.buildSupportEmailSubject
import com.mobaide.app.ui.util.openUrlInCustomTab
import com.mobaide.app.ui.util.sendSupportEmail

@Composable
fun SupportScreen(
    viewModel: SupportViewModel,
    onBack: () -> Unit,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val context = LocalContext.current

    SupportContent(
        onAskCommunity = { openUrlInCustomTab(context, BuildConfig.SUPPORT_GITHUB_URL) },
        onEmailSupport = {
            sendSupportEmail(
                context = context,
                address = BuildConfig.SUPPORT_EMAIL,
                subject = buildSupportEmailSubject(BuildConfig.VERSION_NAME),
                body = buildSupportEmailBody(
                    versionName = BuildConfig.VERSION_NAME,
                    deviceName = uiState.deviceName,
                    deviceId = uiState.deviceId,
                    userEmail = uiState.userEmail,
                ),
            )
        },
        onBack = onBack,
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun SupportContent(
    onAskCommunity: () -> Unit,
    onEmailSupport: () -> Unit,
    onBack: () -> Unit,
) {
    PixelScaffold(
        topBar = {
            PixelTopBar(
                title = { Text("Help & Support") },
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
            Spacer(modifier = Modifier.height(8.dp))

            Text(
                text = "Have a question or hit a bug others might share? Ask the " +
                    "community — public, searchable threads (posting needs a free " +
                    "GitHub account; reading doesn't). Need private or " +
                    "account-specific help? Email support directly.",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(horizontal = 16.dp),
            )

            Spacer(modifier = Modifier.height(16.dp))

            PixelButton(
                onClick = onAskCommunity,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                Text("Ask the community")
            }

            Spacer(modifier = Modifier.height(8.dp))

            PixelButton(
                style = PixelButtonStyle.Primary,
                onClick = onEmailSupport,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                Text("Email support")
            }
        }
    }
}
