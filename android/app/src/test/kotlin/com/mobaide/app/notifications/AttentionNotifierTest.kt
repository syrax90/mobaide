package com.mobaide.app.notifications

import android.Manifest
import android.app.Application
import android.app.Notification
import android.app.NotificationManager
import androidx.test.core.app.ApplicationProvider
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * Copy-mapping tests for [AttentionNotifier.contentFor] via the public
 * [AttentionNotifier.notify] path. Mirrors the existing `awaiting_permission`
 * deserialization coverage in `SessionResponseAttentionTest`, but asserts the
 * user-visible notification title/text the agent's `awaiting_os_permission`
 * heuristic produces.
 *
 * Robolectric (SDK 33) so the real notification post can be read back through
 * the shadow `NotificationManager`; POST_NOTIFICATIONS is granted up front
 * because [AttentionNotifier.notify] is a no-op without it on Tiramisu+.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AttentionNotifierTest {

    private lateinit var application: Application
    private lateinit var manager: NotificationManager
    private lateinit var notifier: AttentionNotifier

    private fun session(
        attention: String?,
        title: String? = null,
        projectName: String? = null,
        exitReason: String? = null,
        kind: SessionKind = SessionKind.Claude,
        kindLabel: String? = null,
    ) = SessionResponse(
        session_id = "s1",
        state = "running",
        created_at = "2026-04-11T10:00:00Z",
        title = title,
        project_name = projectName,
        kind = kind,
        kind_label = kindLabel,
        attention = attention,
        exit_reason = exitReason,
    )

    private fun lastNotification(): Notification {
        val posted = shadowOf(manager).allNotifications
        assertTrue("expected a posted notification", posted.isNotEmpty())
        return posted.last()
    }

    @Before
    fun setUp() {
        application = ApplicationProvider.getApplicationContext()
        shadowOf(application).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
        NotificationChannels.ensureCreated(application)
        manager = application.getSystemService(NotificationManager::class.java)
        notifier = AttentionNotifier(application)
    }

    @Test
    fun awaitingOsPermission_usesMacActionCopy() {
        notifier.notify(session(attention = "awaiting_os_permission", title = "deploy"))

        val n = lastNotification()
        assertEquals(
            "Action needed on your Mac",
            n.extras.getString(Notification.EXTRA_TITLE),
        )
        val body = n.extras.getString(Notification.EXTRA_TEXT).orEmpty()
        assertTrue(body.contains("deploy"))
        assertTrue(body.contains("paused"))
        assertTrue(body.contains("Approve it on the Mac"))
    }

    @Test
    fun awaitingOsPermission_fallsBackToProjectNameLabel() {
        notifier.notify(
            session(attention = "awaiting_os_permission", projectName = "mobaide"),
        )

        val body = lastNotification().extras.getString(Notification.EXTRA_TEXT).orEmpty()
        assertTrue(body.startsWith("mobaide is paused"))
    }

    @Test
    fun awaitingOsPermission_blankLabelsFallBackToDefault() {
        notifier.notify(
            session(attention = "awaiting_os_permission", title = "  ", projectName = ""),
        )

        val body = lastNotification().extras.getString(Notification.EXTRA_TEXT).orEmpty()
        assertTrue(body.startsWith("Claude session is paused"))
    }

    @Test
    fun expandedNotification_showsTheWholeBody() {
        notifier.notify(session(attention = "awaiting_os_permission", title = "deploy"))

        assertEquals(
            "deploy is paused — your Mac is asking to allow file access. Approve it on the Mac to continue.",
            lastNotification().extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString(),
        )
    }

    @Test
    fun turnFailed_usesErrorCopy() {
        notifier.notify(session(attention = "turn_failed", title = "deploy"))

        val n = lastNotification()
        assertEquals("Turn failed", n.extras.getString(Notification.EXTRA_TITLE))
        assertEquals(
            "deploy hit an error — tap to see what happened",
            n.extras.getString(Notification.EXTRA_TEXT),
        )
    }

    @Test
    fun untitledSession_isNamedAfterItsKind() {
        val custom = SessionKind.Custom("custom_0123456789ab")
        val cases = listOf(
            session(attention = "turn_finished", kind = SessionKind.Claude) to "Claude session",
            session(attention = "turn_finished", kind = SessionKind.Codex) to "Codex session",
            session(attention = "turn_finished", kind = SessionKind.Antigravity) to "Antigravity session",
            session(attention = "turn_finished", kind = SessionKind.AutoScan) to "Auto Scan session",
            session(attention = "turn_finished", kind = SessionKind.Shell) to "Terminal session",
            session(attention = "turn_finished", kind = custom, kindLabel = "My Linter") to "My Linter session",
            session(attention = "turn_finished", kind = custom, kindLabel = "  ") to "Agent session",
            session(attention = "turn_finished", kind = custom) to "Agent session",
        )
        for ((untitled, label) in cases) {
            notifier.notify(untitled)
            assertEquals(
                label,
                "$label finished its turn — tap to continue",
                lastNotification().extras.getString(Notification.EXTRA_TEXT),
            )
        }
    }

    @Test
    fun titleAndProject_stillWinOverTheKind() {
        notifier.notify(
            session(attention = "turn_failed", projectName = "mobaide", kind = SessionKind.Codex),
        )

        val body = lastNotification().extras.getString(Notification.EXTRA_TEXT).orEmpty()
        assertTrue(body.startsWith("mobaide hit an error"))
    }

    @Test
    fun unknownAttention_postsNothing() {
        // Unknown reasons fall through to a null content pair → no post, so a
        // future server-side reason can't crash the client.
        notifier.notify(session(attention = "some_future_reason"))
        assertTrue(shadowOf(manager).allNotifications.isEmpty())
    }

    @Test
    fun nullAttention_postsNothing() {
        notifier.notify(session(attention = null))
        assertTrue(shadowOf(manager).allNotifications.isEmpty())
    }

    @Test
    fun awaitingOsPermission_doesNotPostWithoutNotificationPermission() {
        shadowOf(application).denyPermissions(Manifest.permission.POST_NOTIFICATIONS)
        notifier.notify(session(attention = "awaiting_os_permission"))
        assertTrue(shadowOf(manager).allNotifications.isEmpty())
    }

    @Test
    fun awaitingOsPermission_doesNotCarryAnExitReasonSuffix() {
        // exit_reason is an ``ended``-only concern; it must not leak into the
        // os-permission title even when present on the snapshot.
        notifier.notify(
            session(attention = "awaiting_os_permission", exitReason = "process_exited"),
        )
        val title = lastNotification().extras.getString(Notification.EXTRA_TITLE)
        assertEquals("Action needed on your Mac", title)
        assertNull(
            "os-permission title must not gain an (exit_reason) suffix",
            title?.let { if (it.contains("process_exited")) it else null },
        )
    }
}
