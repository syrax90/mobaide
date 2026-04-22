package com.claudemobile.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.claudemobile.app.R
import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import com.claudemobile.app.data.repository.AutoScanRepository
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Foreground service of type `dataSync` that multiplexes the connection
 * status notification together with one child notification per active
 * Auto Scan. Android 14/15 forbids multiple FGS starts of the same type,
 * so we keep a single service and group notifications via setGroup().
 */
@AndroidEntryPoint
class ConnectionService : Service() {

    @Inject lateinit var autoScanRepository: AutoScanRepository

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var observerJob: Job? = null

    /** Per-project last notification update time, for ≥1s/scan throttling. */
    private val lastUpdateAt = mutableMapOf<String, Long>()
    /** Per-project last status — used to flush on transitions even when throttled. */
    private val lastStatus = mutableMapOf<String, String>()
    /** Project names currently visible as child notifications. */
    private val visibleChildren = mutableSetOf<String>()

    override fun onCreate() {
        super.onCreate()
        createNotificationChannels()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFICATION_ID_CONNECTION, buildConnectionNotification())

        if (observerJob == null) {
            observerJob = scope.launch {
                autoScanRepository.observeScans().collect { scans ->
                    syncAutoScanNotifications(scans)
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        observerJob?.cancel()
        observerJob = null
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun createNotificationChannels() {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_CONNECTION,
                "Connection Status",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Shows when Claude Mobile is connected to the agent"
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_AUTOSCAN,
                "Auto Scans",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Shows active Auto Scan sessions"
            },
        )
    }

    private fun buildConnectionNotification(): Notification {
        return NotificationCompat.Builder(this, CHANNEL_CONNECTION)
            .setContentTitle("Claude Mobile")
            .setContentText("Connected to agent")
            .setSmallIcon(R.drawable.ic_notification)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun syncAutoScanNotifications(scans: List<AutoScanInstanceDto>) {
        val nm = NotificationManagerCompat.from(this)
        val activeNames = scans.map { it.project_name }.toSet()

        val gone = visibleChildren - activeNames
        for (name in gone) {
            nm.cancel(notificationIdFor(name))
            lastUpdateAt.remove(name)
            lastStatus.remove(name)
        }
        visibleChildren.removeAll(gone)

        if (scans.isEmpty()) {
            nm.cancel(NOTIFICATION_ID_AUTOSCAN_SUMMARY)
            return
        }

        // Throttle child notification updates to ≥1s per scan, but always flush on status transitions.
        val now = SystemClock.elapsedRealtime()
        for (scan in scans) {
            val prevAt = lastUpdateAt[scan.project_name] ?: 0L
            val prevStatus = lastStatus[scan.project_name]
            val statusChanged = prevStatus != scan.status
            if (!statusChanged && now - prevAt < 1000) continue

            val builder = NotificationCompat.Builder(this, CHANNEL_AUTOSCAN)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle("Auto Scan • ${scan.project_name}")
                .setContentText("${scan.status.replaceFirstChar { it.uppercase() }} · processed ${scan.processed_count}")
                .setGroup(GROUP_AUTOSCAN)
                .setOnlyAlertOnce(true)
                .setOngoing(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)

            try {
                nm.notify(notificationIdFor(scan.project_name), builder.build())
                visibleChildren.add(scan.project_name)
                lastUpdateAt[scan.project_name] = now
                lastStatus[scan.project_name] = scan.status
            } catch (_: SecurityException) {
                // Missing POST_NOTIFICATIONS runtime permission — silently skip.
            }
        }

        val summary = NotificationCompat.Builder(this, CHANNEL_AUTOSCAN)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("Auto Scan")
            .setContentText("${scans.size} active")
            .setStyle(
                NotificationCompat.InboxStyle()
                    .setSummaryText("${scans.size} Auto Scans running")
            )
            .setGroup(GROUP_AUTOSCAN)
            .setGroupSummary(true)
            .setOnlyAlertOnce(true)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        try {
            nm.notify(NOTIFICATION_ID_AUTOSCAN_SUMMARY, summary)
        } catch (_: SecurityException) {
        }
    }

    private fun notificationIdFor(projectName: String): Int =
        NOTIFICATION_ID_AUTOSCAN_BASE + projectName.hashCode().and(0x7fffffff) % 10_000

    companion object {
        private const val CHANNEL_CONNECTION = "connection_status"
        private const val CHANNEL_AUTOSCAN = "autoscan_status"
        private const val GROUP_AUTOSCAN = "autoscan"
        private const val NOTIFICATION_ID_CONNECTION = 1
        private const val NOTIFICATION_ID_AUTOSCAN_SUMMARY = 2
        private const val NOTIFICATION_ID_AUTOSCAN_BASE = 1000

        fun start(context: Context) {
            val intent = Intent(context, ConnectionService::class.java)
            context.startForegroundService(intent)
        }

        fun stop(context: Context) {
            val intent = Intent(context, ConnectionService::class.java)
            context.stopService(intent)
        }
    }
}
