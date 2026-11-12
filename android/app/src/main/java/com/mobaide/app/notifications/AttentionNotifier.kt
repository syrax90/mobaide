package com.mobaide.app.notifications

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.mobaide.app.MainActivity
import com.mobaide.app.R
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Builds and posts the heads-up "session needs you" notification.
 *
 * Tapping deep-links into the session's terminal via [MainActivity]. The
 * notification id is derived from the session id so repeated alerts for one
 * session collapse rather than stack. Posting is a no-op when
 * `POST_NOTIFICATIONS` is denied (Android 13+) — never a crash.
 */
@Singleton
class AttentionNotifier @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    fun notify(session: SessionResponse) {
        val content = contentFor(session) ?: return
        if (!hasPermission()) return

        val tapIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_NEW_TASK
            putExtra(MainActivity.EXTRA_SESSION_ID, session.session_id)
        }
        val notificationId = session.session_id.hashCode()
        val pending = PendingIntent.getActivity(
            context,
            notificationId,
            tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(context, NotificationChannels.SESSION_ALERTS)
            .setSmallIcon(R.drawable.ic_stat_notification)
            .setContentTitle(content.first)
            .setContentText(content.second)
            // Collapsed, Android shows one line of body; the expanded form
            // keeps the end of a long one ("Approve it on the Mac …").
            .setStyle(NotificationCompat.BigTextStyle().bigText(content.second))
            .setAutoCancel(true)
            .setContentIntent(pending)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        try {
            NotificationManagerCompat.from(context).notify(notificationId, notification)
        } catch (_: SecurityException) {
            // POST_NOTIFICATIONS revoked between the check and the post — ignore.
        }
    }

    /** Dismiss a previously-posted alert once the session no longer needs the
     *  user (e.g. they responded from another device), so it doesn't linger. */
    fun cancel(sessionId: String) {
        NotificationManagerCompat.from(context).cancel(sessionId.hashCode())
    }

    private fun contentFor(session: SessionResponse): Pair<String, String>? {
        val label = session.title?.takeIf { it.isNotBlank() }
            ?: session.project_name?.takeIf { it.isNotBlank() }
            ?: fallbackLabel(session)
        return when (session.attention) {
            "awaiting_permission" -> "Permission needed" to "$label is waiting for your approval"
            "awaiting_os_permission" -> "Action needed on your Mac" to
                "$label is paused — your Mac is asking to allow file access. Approve it on the Mac to continue."
            "turn_finished" -> "Your move" to "$label finished its turn — tap to continue"
            "turn_failed" -> "Turn failed" to "$label hit an error — tap to see what happened"
            "ended" -> {
                val reason = session.exit_reason
                    ?.takeIf { it.isNotBlank() }
                    ?.let { " ($it)" }
                    .orEmpty()
                "Session ended$reason" to label
            }
            else -> null
        }
    }

    /** The label for a session with neither a title nor a project. */
    private fun fallbackLabel(session: SessionResponse): String = when (session.kind) {
        SessionKind.Claude -> "Claude session"
        SessionKind.Codex -> "Codex session"
        SessionKind.Antigravity -> "Antigravity session"
        SessionKind.OpenCode -> "OpenCode session"
        SessionKind.Copilot -> "GitHub Copilot session"
        SessionKind.AutoScan -> "Auto Scan session"
        SessionKind.Shell -> "Terminal session"
        is SessionKind.Custom -> session.kind_label?.takeIf { it.isNotBlank() }
            ?.let { "$it session" }
            ?: "Agent session"
    }

    private fun hasPermission(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true
        return ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
    }
}
