package com.mobaide.app.notifications

import android.Manifest
import android.app.Application
import android.app.Notification
import android.app.NotificationManager
import androidx.test.core.app.ApplicationProvider
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.dto.AttentionRequest
import com.mobaide.app.data.network.dto.ChatRespondRequest
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import io.mockk.every
import io.mockk.mockk
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
        workspaceName: String? = null,
        exitReason: String? = null,
        kind: SessionKind = SessionKind.Claude,
        kindLabel: String? = null,
        attentionRequest: AttentionRequest? = null,
    ) = SessionResponse(
        session_id = "s1",
        state = "running",
        created_at = "2026-04-11T10:00:00Z",
        title = title,
        workspace_name = workspaceName,
        kind = kind,
        kind_label = kindLabel,
        attention = attention,
        exit_reason = exitReason,
        attention_request = attentionRequest,
    )

    private fun request(
        choices: List<String> = listOf(ChatRespondRequest.ALLOW, ChatRespondRequest.DENY),
    ) = AttentionRequest(id = "r:3:a1b2c3", title = "npm test", tool_kind = "execute", choices = choices)

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
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getAgentId() } returns "agent-a"
        notifier = AttentionNotifier(application, tokenStore)
    }

    @Test
    fun awaitingOsPermission_blankLabelsFallBackToDefault() {
        notifier.notify(
            session(attention = "awaiting_os_permission", title = "  ", workspaceName = ""),
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
            session(attention = "turn_failed", title = " ", workspaceName = "", kind = SessionKind.Cursor),
        )

        val body = lastNotification().extras.getString(Notification.EXTRA_TEXT).orEmpty()
        assertTrue(body, body.startsWith("Cursor session hit an error"))
    }

    @Test
    fun titleAndWorkspace_stillWinOverTheKind() {
        notifier.notify(
            session(attention = "turn_failed", workspaceName = "mobaide", kind = SessionKind.Codex),
        )

        val body = lastNotification().extras.getString(Notification.EXTRA_TEXT).orEmpty()
        assertTrue(body.startsWith("mobaide hit an error"))
    }

    @Test
    fun answerableChatPermission_offersAllowBehindAnUnlockAndDeny() {
        notifier.notify(
            session(attention = "awaiting_permission", title = "api refactor", attentionRequest = request()),
        )

        val notification = lastNotification()
        assertEquals(
            "api refactor wants to run a command\nnpm test",
            notification.extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString(),
        )
        val actions = notification.actions.orEmpty().toList()
        assertEquals(listOf("Allow", "Deny"), actions.map { it.title.toString() })
        assertEquals(listOf(true, false), actions.map { it.isAuthenticationRequired })
        val answers = actions.map { shadowOf(it.actionIntent).savedIntent }
        assertEquals(listOf("r:3:a1b2c3", "r:3:a1b2c3"), answers.map { it.getStringExtra("request_id") })
        assertEquals(
            listOf(ChatRespondRequest.ALLOW, ChatRespondRequest.DENY),
            answers.map { it.getStringExtra("choice") },
        )
        // The receiver answers only through the agent that posted the alert.
        assertEquals(listOf("agent-a", "agent-a"), answers.map { it.getStringExtra("agent_id") })
    }

    @Test
    fun shownRequestId_namesTheRequestTheShownAlertAnswers() {
        // The receiver skips its failure alert only when the session's shown
        // alert names another request, so a plain alert must read as "".
        assertNull(notifier.shownRequestId("s1"))

        notifier.notify(session(attention = "awaiting_permission", attentionRequest = request()))
        assertEquals("r:3:a1b2c3", notifier.shownRequestId("s1"))

        notifier.notify(session(attention = "turn_finished"))
        assertEquals("", notifier.shownRequestId("s1"))
    }

    @Test
    fun requestThisBuildCannotAnswer_getsAPlainAlert() {
        // A newer agent that offers other choices: no action may send one it
        // did not offer.
        notifier.notify(
            session(
                attention = "awaiting_permission",
                attentionRequest = request(choices = listOf(ChatRespondRequest.ALLOW, "allow_always")),
            ),
        )

        assertTrue(lastNotification().actions.isNullOrEmpty())
    }

    @Test
    fun unknownAttention_postsNothing() {
        // Unknown reasons fall through to a null content pair → no post, so a
        // future server-side reason can't crash the client.
        notifier.notify(session(attention = "some_future_reason"))
        assertTrue(shadowOf(manager).allNotifications.isEmpty())
    }

    @Test
    fun awaitingOsPermission_doesNotPostWithoutNotificationPermission() {
        shadowOf(application).denyPermissions(Manifest.permission.POST_NOTIFICATIONS)
        notifier.notify(session(attention = "awaiting_os_permission"))
        assertTrue(shadowOf(manager).allNotifications.isEmpty())
    }
}
