package com.mobaide.app.notifications

import android.app.Notification
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ProcessLifecycleOwner
import com.mobaide.app.R
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Foreground service that keeps the process (and the app-scoped
 * [AgentEventBus] events socket) alive while the app is backgrounded, so
 * attention notifications still arrive. The bus itself starts/stops from
 * `authState`; this service's job is to hold the process up, show the ongoing
 * "connected" notification, and convert [AgentEvent.SessionAttention] into a
 * system notification.
 *
 * FGS type is `specialUse` (declared in the manifest): a persistent
 * self-hosted notification socket has no standard category, and `dataSync`'s
 * 6-hour Android-15 cap would silently sever delivery. Documented limitation:
 * a swipe-killed or deep-Doze app may still miss events (FCM is deferred).
 */
@AndroidEntryPoint
class SessionEventService : Service() {
    @Inject lateinit var agentEventBus: AgentEventBus
    @Inject lateinit var sessionManager: SessionManager
    @Inject lateinit var notifier: AttentionNotifier
    @Inject lateinit var foregroundSessionTracker: ForegroundSessionTracker

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var started = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!started) {
            started = true
            startForeground(NOTIFICATION_ID, buildPersistentNotification())
            observeAttention()
            observeAuth()
        }
        // NOT sticky: a system-killed process is the documented "may miss
        // events" case; re-starting a FGS from the background would also risk a
        // ForegroundServiceStartNotAllowedException on Android 12+. The service
        // is re-started from the foreground (MainActivity) on next launch.
        return START_NOT_STICKY
    }

    private fun observeAttention() {
        scope.launch {
            agentEventBus.stream<AgentEvent.SessionAttention>().collect { event ->
                val notify = shouldNotify(
                    appForeground = isAppForeground(),
                    visibleSessionId = foregroundSessionTracker.visibleSessionId.value,
                    eventSessionId = event.session.session_id,
                )
                if (notify) notifier.notify(event.session)
            }
        }
        // When attention clears (user responded — possibly from another device),
        // dismiss any alert already posted for that session so it doesn't linger.
        scope.launch {
            agentEventBus.stream<AgentEvent.SessionUpdated>().collect { event ->
                if (event.session.attention == null) {
                    notifier.cancel(event.session.session_id)
                }
            }
        }
    }

    private fun observeAuth() {
        scope.launch {
            sessionManager.authState.collect { state ->
                if (state != SessionManager.AuthState.FullyConnected) {
                    stopForegroundCompat()
                    stopSelf()
                }
            }
        }
    }

    private fun isAppForeground(): Boolean =
        ProcessLifecycleOwner.get().lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)

    private fun buildPersistentNotification(): Notification =
        NotificationCompat.Builder(this, NotificationChannels.CONNECTION_STATUS)
            .setSmallIcon(R.drawable.ic_stat_notification)
            .setContentTitle("Mobaide connected")
            .setContentText("Listening for session alerts")
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

    private fun stopForegroundCompat() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        private const val NOTIFICATION_ID = 1001

        /**
         * Pure suppression rule: notify unless the app is in the foreground AND
         * the user is already looking at the very session the event is for.
         * Alerts for a *different* session fire even when the app is open.
         */
        fun shouldNotify(
            appForeground: Boolean,
            visibleSessionId: String?,
            eventSessionId: String,
        ): Boolean = !(appForeground && visibleSessionId == eventSessionId)

        fun start(context: Context) {
            ContextCompat.startForegroundService(
                context,
                Intent(context, SessionEventService::class.java),
            )
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, SessionEventService::class.java))
        }
    }
}
