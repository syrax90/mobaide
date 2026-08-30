package com.mobaide.app.data.network

import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.auth.UserIdentityStore
import io.mockk.every
import io.mockk.mockk
import okhttp3.OkHttpClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Unit tests for [WebSocketManager.resolveUserEventsUrl], the URL provider
 * behind `ws/user-events`.
 *
 * The regression this pins: an account that has never connected an agent
 * holds neither a relay URL nor an agent URL in [TokenStore], and the user
 * socket must still resolve or the `agent.status_changed` push behind
 * "waiting for the agent to come online" can never arrive.
 */
class WebSocketManagerUserEventsUrlTest {

    private val tokenStore = mockk<TokenStore>()
    private val userIdentityStore = mockk<UserIdentityStore>()

    private fun manager(centralServerUrl: String = "https://central.test") = WebSocketManager(
        client = mockk<OkHttpClient>(),
        tokenStore = tokenStore,
        sessionManager = mockk<SessionManager>(relaxed = true),
        userIdentityStore = userIdentityStore,
        centralServerUrl = centralServerUrl,
    )

    private fun signedIn(token: String? = "T") {
        every { userIdentityStore.isUserLoggedIn() } returns (token != null)
        every { userIdentityStore.getUserAccessToken() } returns token
    }

    private fun directMode(agentUrl: String?) {
        every { tokenStore.getConnectionMode() } returns TokenStore.ConnectionMode.DIRECT
        every { tokenStore.getAgentUrl() } returns agentUrl
    }

    private fun relayMode(relayUrl: String?) {
        every { tokenStore.getConnectionMode() } returns TokenStore.ConnectionMode.RELAY
        every { tokenStore.getRelayServerUrl() } returns relayUrl
    }

    @Test
    fun resolveUserEventsUrl_directModeWithNoAgentUrl_fallsBackToCentralServer() {
        // A fresh install: signed in, never connected an agent. Before the
        // fallback this returned null and the user socket never opened.
        signedIn()
        directMode(agentUrl = null)

        val url = manager().resolveUserEventsUrl("ws/user-events")

        assertEquals("wss://central.test/ws/user-events?token=T", url)
    }

    @Test
    fun resolveUserEventsUrl_directModeWithAgentUrl_keepsAgentUrl() {
        // Single-agent dev setup where the agent fronts the user API (iOS
        // parity): the stored agent URL wins over the central fallback.
        signedIn()
        directMode(agentUrl = "http://10.0.2.2:8000")

        val url = manager().resolveUserEventsUrl("ws/user-events")

        assertEquals("ws://10.0.2.2:8000/ws/user-events?token=T", url)
    }

    @Test
    fun resolveUserEventsUrl_relayMode_usesStoredRelayUrl() {
        signedIn()
        relayMode(relayUrl = "https://relay.example")

        val url = manager().resolveUserEventsUrl("ws/user-events")

        assertEquals("wss://relay.example/ws/user-events?token=T", url)
    }

    @Test
    fun resolveUserEventsUrl_relayModeWithNoStoredUrl_fallsBackToCentralServer() {
        // After SessionManager.agentDisconnect the relay URL is gone but the
        // account is still signed in; presence must keep flowing.
        signedIn()
        relayMode(relayUrl = null)

        val url = manager().resolveUserEventsUrl("ws/user-events")

        assertEquals("wss://central.test/ws/user-events?token=T", url)
    }

    @Test
    fun resolveUserEventsUrl_signedOut_returnsNull() {
        signedIn(token = null)
        directMode(agentUrl = null)

        assertNull(manager().resolveUserEventsUrl("ws/user-events"))
    }

    @Test
    fun resolveUserEventsUrl_pathWithQuery_appendsTokenWithAmpersand() {
        signedIn()
        relayMode(relayUrl = "https://relay.example")

        val url = manager().resolveUserEventsUrl("ws/user-events?v=2")

        assertEquals("wss://relay.example/ws/user-events?v=2&token=T", url)
    }

    @Test
    fun resolveUserEventsUrl_trailingSlashOnBase_doesNotDoubleTheSeparator() {
        signedIn()
        directMode(agentUrl = null)

        val url = manager(centralServerUrl = "https://central.test/")
            .resolveUserEventsUrl("ws/user-events")

        assertEquals("wss://central.test/ws/user-events?token=T", url)
    }
}
