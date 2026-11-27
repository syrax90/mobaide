package com.mobaide.app.data.network

import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.auth.UserIdentityStore
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import okhttp3.OkHttpClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Unit tests for [WebSocketManager.resolveUserEventsUrl], the URL provider
 * behind `ws/user-events`.
 *
 * Pins that an account with no relay URL in [TokenStore] still resolves the
 * user socket, so `agent.status_changed` can arrive.
 */
class WebSocketManagerUserEventsUrlTest {

    private val tokenStore = mockk<TokenStore>()
    private val userIdentityStore = mockk<UserIdentityStore>()

    private fun manager(centralServerUrl: String = "https://central.test") = WebSocketManager(
        client = mockk<OkHttpClient>(),
        tokenStore = tokenStore,
        sessionManager = mockk<SessionManager>(relaxed = true),
        userIdentityStore = userIdentityStore,
        userClient = mockk<OkHttpClient>(),
        centralServerUrl = centralServerUrl,
    )

    private fun signedIn(token: String? = "T") {
        every { userIdentityStore.isUserLoggedIn() } returns (token != null)
        every { userIdentityStore.getUserAccessToken() } returns token
    }

    private fun storedRelayUrl(relayUrl: String?) {
        every { tokenStore.getRelayServerUrl() } returns relayUrl
    }

    @Test
    fun resolveUserEventsUrl_usesStoredRelayUrl() {
        signedIn()
        storedRelayUrl("https://relay.example")

        val url = manager().resolveUserEventsUrl("ws/user-events")

        assertEquals("wss://relay.example/ws/user-events?token=T", url)
    }

    @Test
    fun resolveUserEventsUrl_withNoStoredUrl_fallsBackToCentralServer() {
        // A fresh install that never connected an agent, and any account
        // after SessionManager.agentDisconnect: no relay URL is stored but
        // the account is signed in. Before the fallback this returned null
        // and the user socket never opened.
        signedIn()
        storedRelayUrl(null)

        val url = manager().resolveUserEventsUrl("ws/user-events")

        assertEquals("wss://central.test/ws/user-events?token=T", url)
    }

    @Test
    fun resolveUserEventsUrl_signedOut_returnsNull() {
        signedIn(token = null)
        storedRelayUrl(null)

        assertNull(manager().resolveUserEventsUrl("ws/user-events"))
    }

    @Test
    fun resolveUserEventsUrl_trailingSlashOnBase_doesNotDoubleTheSeparator() {
        signedIn()
        storedRelayUrl(null)

        val url = manager(centralServerUrl = "https://central.test/")
            .resolveUserEventsUrl("ws/user-events")

        assertEquals("wss://central.test/ws/user-events?token=T", url)
    }

    @Test
    fun createUserConnection_dialsThroughTheUserClient_neverTheAgentClient() {
        // The agent-scoped client's interceptor answers every request made
        // before an agent is connected with a synthetic 401, which the socket
        // reads as a rejected token — dialling through it signed users out
        // right after login once the channel started opening in UserOnly.
        signedIn()
        storedRelayUrl(null)
        val agentClient = mockk<OkHttpClient>(relaxed = true)
        val userClient = mockk<OkHttpClient>(relaxed = true)
        val manager = WebSocketManager(
            client = agentClient,
            tokenStore = tokenStore,
            sessionManager = mockk<SessionManager>(relaxed = true),
            userIdentityStore = userIdentityStore,
            userClient = userClient,
            centralServerUrl = "https://central.test",
        )

        manager.createUserConnection("ws/user-events").connect()

        verify(timeout = 2_000) { userClient.newWebSocket(any(), any()) }
        verify(exactly = 0) { agentClient.newWebSocket(any(), any()) }
    }
}
