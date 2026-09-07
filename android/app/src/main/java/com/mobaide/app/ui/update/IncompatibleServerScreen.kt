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
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.PixelScaffold
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
    PixelScaffold { padding ->
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
            // `state.reason` is server-controlled on the 4002 close path, where in
            // direct mode the peer is the paired agent host. Nothing reads it; it
            // stays only for state parity with iOS and web. Do not render it.
            //
            // The gate is set membership, not a floor: it also fires when the app
            // is newer than the relay, so the copy must not name a direction.
            Text(
                text = "This app version can no longer connect to the server. " +
                    "Check for an update to continue.",
                style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(8.dp))
            PixelButton(
                onClick = {
                    openPlayStore(context, state.playStoreUrl, state.playStoreFallbackUrl)
                },
                style = PixelButtonStyle.Primary,
            ) {
                Text("Update from Play Store")
            }
            PixelButton(onClick = {
                openExternalUrl(
                    context,
                    "https://mobaide.com/docs/protocol",
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
