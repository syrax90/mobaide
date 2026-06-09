package com.mobaide.app.notifications

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context

/**
 * Notification channels. Created once at app startup (minSdk is 26, so
 * channels are always available — no version guard needed).
 *
 * - [SESSION_ALERTS]: HIGH importance, the heads-up "your move" / permission /
 *   ended pop-ups.
 * - [CONNECTION_STATUS]: LOW importance, the ongoing notification the
 *   foreground service must show while it holds the events socket.
 */
object NotificationChannels {
    const val SESSION_ALERTS = "session_alerts"
    const val CONNECTION_STATUS = "connection_status"

    fun ensureCreated(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(
                SESSION_ALERTS,
                "Session alerts",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Claude needs your input, finished a turn, or a session ended"
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(
                CONNECTION_STATUS,
                "Connection status",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Keeps the connection alive in the background to deliver alerts"
            },
        )
    }
}
