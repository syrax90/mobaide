package com.mobaide.app.notifications

import android.Manifest
import android.app.Application
import android.app.Notification
import android.app.NotificationManager
import androidx.test.core.app.ApplicationProvider
import com.mobaide.app.MainActivity
import com.mobaide.app.data.network.dto.HerdrAttention
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import org.junit.Assert.assertEquals
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
    fun untitledCursorSession_isNamedAfterItsKind() {
        notifier.notify(
            session(attention = "turn_failed", title = " ", projectName = "", kind = SessionKind.Cursor),
        )

        val body = lastNotification().extras.getString(Notification.EXTRA_TEXT).orEmpty()
        assertTrue(body, body.startsWith("Cursor session hit an error"))
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

    private fun herdrAttention(
        paneId: String = "w1:p1",
        reason: String = "awaiting_permission",
        title: String = "api refactor",
    ) = HerdrAttention(server_id = "tui:default", pane_id = paneId, reason = reason, title = title)

    @Test
    fun herdrAlert_tapCarriesTheServerAndThePane() {
        // A pane has no session: the tap must name the pane, or it opens nothing.
        notifier.notifyHerdr(herdrAttention(paneId = "box/t3"))

        val posted = lastNotification()
        val tap = shadowOf(posted.contentIntent).savedIntent
        assertEquals("tui:default", tap.getStringExtra(MainActivity.EXTRA_HERDR_SERVER_ID))
        assertEquals("box/t3", tap.getStringExtra(MainActivity.EXTRA_HERDR_PANE_ID))
        assertEquals(null, tap.getStringExtra(MainActivity.EXTRA_SESSION_ID))
        assertEquals(
            "api refactor is waiting for your approval",
            posted.extras.getString(Notification.EXTRA_TEXT),
        )
    }

    @Test
    fun herdrAlerts_ofTwoPanes_standSideBySide_andCancelRemovesOnlyItsOwn() {
        notifier.notifyHerdr(herdrAttention(paneId = "w1:p1"))
        notifier.notifyHerdr(herdrAttention(paneId = "w1:p2", title = "landing page"))
        assertEquals(2, shadowOf(manager).allNotifications.size)

        notifier.cancelHerdr("tui:default", "w1:p1")

        val left = shadowOf(manager).allNotifications.single()
        assertEquals(
            "landing page is waiting for your approval",
            left.extras.getString(Notification.EXTRA_TEXT),
        )
    }

    @Test
    fun herdrAlert_forAPaneWithoutATitle_usesTheLabelOfThePush() {
        // The server names an untitled pane "Herdr agent" in its push.
        notifier.notifyHerdr(herdrAttention(reason = "turn_failed", title = " "))

        val body = lastNotification().extras.getString(Notification.EXTRA_TEXT).orEmpty()
        assertTrue(body, body.startsWith("Herdr agent hit an error"))
    }

    @Test
    fun herdrAlert_readsAsTheSessionAlertOfTheSameReason() {
        // One text table for sessions and panes: a reason must not read one
        // way for a session and another way for the same agent in a pane.
        fun posted(post: () -> Unit): Pair<String?, String?> {
            manager.cancelAll()
            post()
            val extras = shadowOf(manager).allNotifications.single().extras
            return extras.getString(Notification.EXTRA_TITLE) to extras.getString(Notification.EXTRA_TEXT)
        }

        for (reason in listOf("awaiting_permission", "turn_finished", "turn_failed")) {
            val forSession = posted { notifier.notify(session(attention = reason, title = "api refactor")) }
            val forPane = posted { notifier.notifyHerdr(herdrAttention(reason = reason, title = "api refactor")) }

            assertEquals(reason, forSession, forPane)
            assertTrue(reason, forPane.second.orEmpty().startsWith("api refactor "))
        }
    }

    @Test
    fun herdrAlert_unknownReason_postsNothing() {
        notifier.notifyHerdr(herdrAttention(reason = "some_future_reason"))

        assertTrue(shadowOf(manager).allNotifications.isEmpty())
    }

    @Test
    fun awaitingOsPermission_doesNotPostWithoutNotificationPermission() {
        shadowOf(application).denyPermissions(Manifest.permission.POST_NOTIFICATIONS)
        notifier.notify(session(attention = "awaiting_os_permission"))
        assertTrue(shadowOf(manager).allNotifications.isEmpty())
    }
}
