package com.mobaide.app.notifications

import android.Manifest
import android.app.Application
import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.ChatRespondRequest
import dagger.hilt.android.EntryPointAccessors
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.mockkStatic
import io.mockk.unmockkStatic
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadow.api.Shadow
import org.robolectric.shadows.ShadowBroadcastPendingResult
import org.robolectric.shadows.ShadowBroadcastReceiver
import java.util.concurrent.TimeUnit

/**
 * An Allow or Deny tap removes the alert it came from, but never the alert of
 * a newer request of the same session: Android 12+ delivers an Allow only
 * after the unlock, and the next request's alert can show by then.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PermissionActionReceiverTest {

    private lateinit var application: Application
    private lateinit var notifier: AttentionNotifier

    @Before
    fun setUp() {
        application = ApplicationProvider.getApplicationContext()
        shadowOf(application).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
        NotificationChannels.ensureCreated(application)
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getAgentId() } returns AGENT
        val restApi = mockk<RestApi>()
        coEvery { restApi.respondToChatRequest(any(), any()) } just Runs
        notifier = AttentionNotifier(application, tokenStore)
        val deps = object : PermissionActionReceiver.Dependencies {
            override fun restApi() = restApi
            override fun notifier() = notifier
            override fun tokenStore() = tokenStore
        }
        mockkStatic(EntryPointAccessors::class)
        every {
            EntryPointAccessors.fromApplication(any(), PermissionActionReceiver.Dependencies::class.java)
        } returns deps
    }

    @After
    fun tearDown() {
        unmockkStatic(EntryPointAccessors::class)
    }

    private fun target(requestId: String) = PermissionActionReceiver.Target(
        sessionId = SESSION,
        requestId = requestId,
        title = "Permission needed",
        body = "api refactor wants to run a command\nnpm test",
        agentId = AGENT,
    )

    private fun showAlertFor(requestId: String) {
        val alert = target(requestId)
        notifier.post(alert.sessionId, alert.title, alert.body, alert.requestId)
    }

    /** Delivers the action and waits until the receiver's answer ends. */
    private fun tap(requestId: String, choice: String) {
        val intent = PermissionActionReceiver.intent(application, target(requestId), choice)
        val receiver = shadowOf(application).registeredReceivers
            .map { it.broadcastReceiver }
            .filterIsInstance<PermissionActionReceiver>()
            .single()
        application.sendBroadcast(intent)
        shadowOf(Looper.getMainLooper()).idle()
        val pending = Shadow.extract<ShadowBroadcastReceiver>(receiver).originalPendingResult
        Shadow.extract<ShadowBroadcastPendingResult>(pending).future.get(5, TimeUnit.SECONDS)
    }

    @Test
    fun lateAnswer_keepsTheAlertOfANewerRequest() {
        showAlertFor(NEWER)

        tap(OLDER, ChatRespondRequest.ALLOW)

        assertEquals(NEWER, notifier.shownRequestId(SESSION))
    }

    @Test
    fun answer_removesTheAlertItCameFrom() {
        // A second tap on the same alert must not send a second answer.
        showAlertFor(OLDER)

        tap(OLDER, ChatRespondRequest.ALLOW)

        assertNull(notifier.shownRequestId(SESSION))
    }

    private companion object {
        const val SESSION = "s1"
        const val AGENT = "agent-a"
        const val OLDER = "r:1:aaaaaa"
        const val NEWER = "r:2:bbbbbb"
    }
}
