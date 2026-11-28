package com.mobaide.app.notifications

import android.Manifest
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.core.os.bundleOf
import com.mobaide.app.MainActivity
import com.mobaide.app.R
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.dto.ChatRespondRequest
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
 *
 * A chat session waiting on one plain tool permission (`attention_request`)
 * gets Allow and Deny actions, answered by [PermissionActionReceiver].
 */
@Singleton
class AttentionNotifier @Inject constructor(
    @ApplicationContext private val context: Context,
    private val tokenStore: TokenStore,
) {
    fun notify(session: SessionResponse) {
        val content = contentFor(session) ?: return
        val requestId = session.attention_request
            ?.takeIf { session.attention == "awaiting_permission" && it.isAnswerable }
            ?.id
        post(session.session_id, content.first, content.second, requestId)
    }

    /**
     * Posts (or replaces) a session's alert. With [requestId], it also offers
     * Allow and Deny for that chat request, on the agent connected now.
     * [alertOnce]: no sound or vibration when the alert already shows.
     */
    fun post(
        sessionId: String,
        title: String,
        body: String,
        requestId: String? = null,
        alertOnce: Boolean = false,
    ) {
        if (!hasPermission()) return
        val notificationId = sessionId.hashCode()
        val tapIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_NEW_TASK
            putExtra(MainActivity.EXTRA_SESSION_ID, sessionId)
        }
        val pending = PendingIntent.getActivity(
            context,
            notificationId,
            tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = NotificationCompat.Builder(context, NotificationChannels.SESSION_ALERTS)
            .setSmallIcon(R.drawable.ic_stat_notification)
            .setContentTitle(title)
            .setContentText(body.substringBefore('\n'))
            // Collapsed, Android shows one line of body; the expanded form
            // keeps the end of a long one ("Approve it on the Mac …") and the
            // request's own title on its second line.
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setContentIntent(pending)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setOnlyAlertOnce(alertOnce)
        if (requestId != null) {
            builder.addExtras(bundleOf(EXTRA_REQUEST_ID to requestId))
            val target = PermissionActionReceiver.Target(
                sessionId,
                requestId,
                title,
                body,
                agentId = tokenStore.getAgentId(),
            )
            // Allow needs the device unlocked first; the receiver checks it
            // too. Deny stays usable when locked.
            builder.addAction(action("Allow", target, ChatRespondRequest.ALLOW, authenticate = true))
            builder.addAction(action("Deny", target, ChatRespondRequest.DENY, authenticate = false))
        }
        try {
            NotificationManagerCompat.from(context).notify(notificationId, builder.build())
        } catch (_: SecurityException) {
            // POST_NOTIFICATIONS revoked between the check and the post — ignore.
        }
    }

    private fun action(
        label: String,
        target: PermissionActionReceiver.Target,
        choice: String,
        authenticate: Boolean,
    ): NotificationCompat.Action {
        val intent = PermissionActionReceiver.intent(context, target, choice)
        // One request code per request and choice: a newer alert's actions
        // never reuse an older PendingIntent's extras.
        val pending = PendingIntent.getBroadcast(
            context,
            "${target.requestId}|$choice".hashCode(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Action.Builder(0, label, pending)
            .setAuthenticationRequired(authenticate)
            .build()
    }

    /**
     * The request the session's shown alert offers Allow and Deny for: null
     * when no alert of the session shows, `""` when it offers none.
     */
    fun shownRequestId(sessionId: String): String? {
        val id = sessionId.hashCode()
        val shown = context.getSystemService(NotificationManager::class.java)
            ?.activeNotifications
            ?.firstOrNull { it.id == id }
            ?: return null
        return shown.notification.extras.getString(EXTRA_REQUEST_ID).orEmpty()
    }

    /** Dismiss a previously-posted alert once the session no longer needs the
     *  user (e.g. they responded from another device), so it doesn't linger. */
    fun cancel(sessionId: String) {
        NotificationManagerCompat.from(context).cancel(sessionId.hashCode())
    }

    private fun contentFor(session: SessionResponse): Pair<String, String>? {
        val label = session.title?.takeIf { it.isNotBlank() }
            ?: session.workspace_name?.takeIf { it.isNotBlank() }
            ?: fallbackLabel(session)
        return when (session.attention) {
            "awaiting_permission" -> "Permission needed" to permissionBody(label, session)
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

    /**
     * "<label> wants to run a command", then the request's own title on a
     * second line. The server's `push/content.py` sends the same words over
     * APNs, without the title.
     */
    private fun permissionBody(label: String, session: SessionResponse): String {
        val request = session.attention_request ?: return "$label is waiting for your approval"
        val phrase = PERMISSION_PHRASES[request.tool_kind] ?: "is waiting for your approval"
        val title = request.title.trim()
        return if (title.isEmpty()) "$label $phrase" else "$label $phrase\n$title"
    }

    /** The label for a session with neither a title nor a workspace. */
    private fun fallbackLabel(session: SessionResponse): String = when (session.kind) {
        SessionKind.Claude -> "Claude session"
        SessionKind.Codex -> "Codex session"
        SessionKind.Antigravity -> "Antigravity session"
        SessionKind.OpenCode -> "OpenCode session"
        SessionKind.Copilot -> "GitHub Copilot session"
        SessionKind.Cursor -> "Cursor session"
        SessionKind.Junie -> "Junie session"
        SessionKind.Pi -> "Pi session"
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

    private companion object {
        const val EXTRA_REQUEST_ID = "com.mobaide.app.request_id"

        /** What a chat request asks for, by its ACP tool kind. */
        val PERMISSION_PHRASES = mapOf(
            "execute" to "wants to run a command",
            "edit" to "wants to edit files",
            "delete" to "wants to delete files",
            "move" to "wants to move files",
            "read" to "wants to read files",
            "search" to "wants to search files",
            "fetch" to "wants to fetch from the web",
        )
    }
}
