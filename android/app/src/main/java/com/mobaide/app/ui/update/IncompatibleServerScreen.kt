package com.mobaide.app.ui.update

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.auth.SessionManager

@Composable
fun IncompatibleServerScreen(
    state: SessionManager.AuthState.IncompatibleServer,
    onImmediateUpdate: () -> Unit = {},
) {
    val context = LocalContext.current
    // Trigger Play Core's IMMEDIATE in-app update flow once on entry — the
    // server-driven gate has already determined this app is too old, so we
    // attempt a hard update before falling back to the Play Store deep link.
    // Sideloaded builds (Play has no update record) silently no-op.
    LaunchedEffect(Unit) { onImmediateUpdate() }
    Scaffold { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = "App update required",
                style = MaterialTheme.typography.headlineMedium,
                textAlign = TextAlign.Center,
            )
            Text(
                text = state.reason,
                style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(8.dp))
            Button(onClick = {
                openPlayStore(context, state.playStoreUrl, state.playStoreFallbackUrl)
            }) {
                Text("Update from Play Store")
            }
            OutlinedButton(onClick = {
                openExternalUrl(
                    context,
                    "https://github.com/syrax90/claude-mobile/blob/main/PROTOCOL.md",
                )
            }) {
                Text("What changed")
            }
        }
    }
}

private fun openPlayStore(context: android.content.Context, primary: String, fallback: String) {
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(primary)).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        })
    } catch (_: ActivityNotFoundException) {
        openExternalUrl(context, fallback)
    }
}

private fun openExternalUrl(context: android.content.Context, url: String) {
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        })
    } catch (_: ActivityNotFoundException) {
    }
}
