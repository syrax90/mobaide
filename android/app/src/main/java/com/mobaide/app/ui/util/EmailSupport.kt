package com.mobaide.app.ui.util

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.widget.Toast
import androidx.core.net.toUri
import com.mobaide.app.ui.common.copyToClipboard

/**
 * Opens the user's email app to compose a private support message, pre-filled
 * with the support address, a versioned subject, and a diagnostics body.
 *
 * ACTION_SENDTO with a bare `mailto:` data URI restricts the chooser to email
 * apps only. resolveActivity is accurate here because the manifest declares a
 * matching `<queries>` intent (required under API 30+ package visibility);
 * when it reports no email client, the address is copied to the clipboard and a
 * toast explains the fallback so the user can still reach support. `mailto:`
 * cannot carry attachments, so diagnostics are embedded as body text.
 */
fun sendSupportEmail(context: Context, address: String, subject: String, body: String) {
    val sendto = Intent(Intent.ACTION_SENDTO).apply {
        data = "mailto:".toUri()
        putExtra(Intent.EXTRA_EMAIL, arrayOf(address))
        putExtra(Intent.EXTRA_SUBJECT, subject)
        putExtra(Intent.EXTRA_TEXT, body)
    }
    if (sendto.resolveActivity(context.packageManager) != null) {
        try {
            context.startActivity(Intent.createChooser(sendto, "Email support"))
            return
        } catch (_: ActivityNotFoundException) {
            // resolveActivity matched but the app vanished between the check
            // and launch; fall through to the clipboard fallback.
        }
    }
    context.copyToClipboard(label = "Support email", text = address)
    Toast.makeText(
        context,
        "No email app found — support address copied to clipboard",
        Toast.LENGTH_LONG,
    ).show()
}

fun buildSupportEmailSubject(versionName: String): String = "Mobaide support — v$versionName"

/**
 * Builds the support email body: a free-text prompt followed by a diagnostics
 * block. The signed-in account line is appended only when [userEmail] is
 * present, keeping the body valid for anonymous (signed-out) reporters.
 */
fun buildSupportEmailBody(
    versionName: String,
    deviceName: String,
    deviceId: String,
    userEmail: String?,
): String = buildString {
    appendLine("Describe your question or issue here:")
    appendLine()
    appendLine()
    appendLine("———")
    appendLine("Diagnostics (please keep — helps us troubleshoot):")
    appendLine("App version: $versionName")
    appendLine("Device: $deviceName")
    appendLine("Device ID: $deviceId")
    if (!userEmail.isNullOrBlank()) {
        appendLine("Account: $userEmail")
    }
}
