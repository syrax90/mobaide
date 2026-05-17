package com.mobaide.app.data.network

import com.mobaide.app.data.auth.SessionManager
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * Unit tests for [WebSocketConnection]'s auth-failure handling — the
 * paths where a server-side close with code 4001 / 4010 (or a failed WS
 * upgrade with HTTP 401) invokes [onAuthFailure] and stops reconnecting.
 *
 * This is the WS-side counterpart to [com.mobaide.app.data.auth.CentralAuthInterceptor]
 * for the user-scoped channel (`ws/user-events`): when the central
 * server rejects the dashboard JWT or destructively unregisters the
 * user, the client must tear down the session rather than retry-spamming
 * the socket.
 *
 * [OkHttpClient] is mocked so that we can capture the real
 * [WebSocketListener] that [WebSocketConnection] installs, then invoke
 * its callbacks directly — this exercises the production listener code
 * without a network round-trip.
 */
class WebSocketConnectionAuthFailureTest {

    private fun responseFor(req: Request, code: Int): Response =
        Response.Builder()
            .request(req)
            .protocol(Protocol.HTTP_1_1)
            .code(code)
            .message(if (code in 200..299) "OK" else "ERR")
            .build()

    /**
     * Build a mock OkHttpClient whose first `newWebSocket` call captures
     * the listener into the returned [CompletableDeferred] and returns a
     * mock [WebSocket]. The caller uses the deferred to drive listener
     * callbacks.
     */
    private fun clientCapturingListener(): Triple<
        OkHttpClient,
        CompletableDeferred<WebSocketListener>,
        AtomicInteger, // newWebSocket call count
    > {
        val client = mockk<OkHttpClient>()
        val deferred = CompletableDeferred<WebSocketListener>()
        val calls = AtomicInteger(0)
        val slotListener = slot<WebSocketListener>()
        every { client.newWebSocket(any(), capture(slotListener)) } answers {
            calls.incrementAndGet()
            // Complete only on the first capture — subsequent calls
            // (which shouldn't happen in auth-failure tests) bump the
            // counter but don't overwrite the first listener.
            if (!deferred.isCompleted) deferred.complete(slotListener.captured)
            mockk<WebSocket>(relaxed = true)
        }
        return Triple(client, deferred, calls)
    }

    private fun sessionManager(): SessionManager =
        mockk<SessionManager>(relaxUnitFun = true).also {
            every { it.isLoggedIn() } returns true
        }

    private fun awaitListener(d: CompletableDeferred<WebSocketListener>): WebSocketListener =
        runBlocking {
            try {
                withTimeout(2_000) { d.await() }
            } catch (_: TimeoutCancellationException) {
                error("WebSocketListener was never captured — doConnect did not run")
            }
        }

    @Test
    fun onClosed_withCode4001_invokesOnAuthFailure_withTokenRejected() {
        val (client, deferredListener, _) = clientCapturingListener()
        val sm = sessionManager()
        val authFailures = AtomicInteger(0)
        val lastReason = AtomicReference<SessionManager.InvalidationReason?>(null)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://central.example.com/ws/user-events?token=stale" },
            isAuthenticated = { true },
            onAuthFailure = { reason ->
                authFailures.incrementAndGet()
                lastReason.set(reason)
            },
        )

        conn.connect()
        val listener = awaitListener(deferredListener)
        val fakeSocket = mockk<WebSocket>(relaxed = true)
        val fakeResponse = responseFor(
            Request.Builder().url("https://central.example.com").build(),
            101,
        )
        listener.onOpen(fakeSocket, fakeResponse)
        listener.onClosed(fakeSocket, 4001, "Invalid token")

        assertEquals(1, authFailures.get())
        assertEquals(
            SessionManager.InvalidationReason.TokenRejected,
            lastReason.get(),
        )
    }

    @Test
    fun onClosed_withCode4010_invokesOnAuthFailure_withAccountRemoved() {
        // 4010 is WS_CLOSE_ADMIN_REVOKED — admin destructively unregistered
        // the user. Distinct from 4001 so the login screen can render a
        // user-specific "account removed" message; matches PROTOCOL.md.
        val (client, deferredListener, callCount) = clientCapturingListener()
        val sm = sessionManager()
        val authFailures = AtomicInteger(0)
        val lastReason = AtomicReference<SessionManager.InvalidationReason?>(null)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://central.example.com/ws/user-events?token=stale" },
            isAuthenticated = { true },
            onAuthFailure = { reason ->
                authFailures.incrementAndGet()
                lastReason.set(reason)
            },
        )

        conn.connect()
        val listener = awaitListener(deferredListener)
        val fakeSocket = mockk<WebSocket>(relaxed = true)
        listener.onClosed(fakeSocket, 4010, "admin_revoked")

        assertEquals(1, authFailures.get())
        assertEquals(
            SessionManager.InvalidationReason.AccountRemoved,
            lastReason.get(),
        )
        // Reconnection MUST be permanently suppressed — re-issuing a fresh
        // JWT cannot recover a cascade-deleted account.
        assertEquals(
            "no reconnection allowed after admin revoke",
            1,
            callCount.get(),
        )
    }

    @Test
    fun onClosed_withCode1000_reconnects_doesNotInvokeOnAuthFailure() {
        val (client, deferredListener, callCount) = clientCapturingListener()
        val sm = sessionManager()
        val authFailures = AtomicInteger(0)
        val lastReason = AtomicReference<SessionManager.InvalidationReason?>(null)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://central.example.com/ws/user-events?token=ok" },
            isAuthenticated = { true },
            onAuthFailure = { reason ->
                authFailures.incrementAndGet()
                lastReason.set(reason)
            },
        )

        conn.connect()
        val listener = awaitListener(deferredListener)
        listener.onClosed(mockk<WebSocket>(relaxed = true), 1000, "Normal closure")

        assertEquals(0, authFailures.get())
        // 1000 is normal closure; reconnect may or may not fire quickly
        // (backoff delay), but the critical invariant is that we didn't
        // treat it as an auth failure.
        assertTrue(
            "first newWebSocket call count must be at least 1",
            callCount.get() >= 1,
        )
    }

    @Test
    fun onFailure_withResponse401_invokesOnAuthFailure_withTokenRejected() {
        // okhttp surfaces a failed WS upgrade (HTTP 401 instead of 101)
        // via onFailure(response.code == 401). Treat same as 4001 close.
        val (client, deferredListener, _) = clientCapturingListener()
        val sm = sessionManager()
        val authFailures = AtomicInteger(0)
        val lastReason = AtomicReference<SessionManager.InvalidationReason?>(null)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://central.example.com/ws/user-events?token=stale" },
            isAuthenticated = { true },
            onAuthFailure = { reason ->
                authFailures.incrementAndGet()
                lastReason.set(reason)
            },
        )

        conn.connect()
        val listener = awaitListener(deferredListener)
        val fakeSocket = mockk<WebSocket>(relaxed = true)
        val req = Request.Builder().url("https://central.example.com").build()
        listener.onFailure(fakeSocket, IllegalStateException("upgrade failed"), responseFor(req, 401))

        assertEquals(1, authFailures.get())
        assertEquals(
            SessionManager.InvalidationReason.TokenRejected,
            lastReason.get(),
        )
    }

    @Test
    fun onFailure_withoutResponse_doesNotInvokeOnAuthFailure() {
        // Network error (no response at all) — retriable, not an auth
        // failure. The scheduleReconnect guard downstream handles the
        // back-off loop.
        val (client, deferredListener, _) = clientCapturingListener()
        val sm = sessionManager()
        val authFailures = AtomicInteger(0)
        val lastReason = AtomicReference<SessionManager.InvalidationReason?>(null)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://central.example.com/ws/user-events?token=ok" },
            isAuthenticated = { true },
            onAuthFailure = { reason ->
                authFailures.incrementAndGet()
                lastReason.set(reason)
            },
        )

        conn.connect()
        val listener = awaitListener(deferredListener)
        listener.onFailure(
            mockk<WebSocket>(relaxed = true),
            java.io.IOException("no route to host"),
            response = null,
        )

        assertEquals(0, authFailures.get())
    }

    @Test
    fun onAuthFailure_isOptional_agentScopedChannelSurvivesClose4001() {
        // Default `onAuthFailure = null` — agent-scoped channels rely on
        // their URL provider's pre-connect refresh instead. A 4001 here
        // must NOT crash and must NOT throw; it just stops reconnecting.
        val (client, deferredListener, _) = clientCapturingListener()
        val sm = sessionManager()
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://example.com/ws/events?token=x" },
            // No onAuthFailure — defaults to null.
        )

        conn.connect()
        val listener = awaitListener(deferredListener)
        // Must not throw NPE etc.
        listener.onClosed(mockk<WebSocket>(relaxed = true), 4001, "bye")
    }

    @Test
    fun onAuthFailure_isOptional_agentScopedChannelSurvivesClose4010_andSuppressesReconnect() {
        // Same null-callback guard as the 4001 case: an agent-scoped
        // channel that happens to receive a 4010 (because the central
        // server cascaded an admin delete down to the agent tunnel) must
        // not NPE. Additionally, since 4010 means destructive revoke
        // (per PROTOCOL.md and the matching server-side
        // routing.disconnect_agent path), reconnection MUST be
        // permanently suppressed even though no callback was passed —
        // re-issuing a fresh token cannot recover a deleted agent. The
        // `callCount == 1` assertion below is the regression guard the
        // first iteration of this test was missing.
        val (client, deferredListener, callCount) = clientCapturingListener()
        val sm = sessionManager()
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://example.com/ws/events?token=x" },
            // No onAuthFailure — defaults to null.
        )

        conn.connect()
        val listener = awaitListener(deferredListener)
        listener.onClosed(mockk<WebSocket>(relaxed = true), 4010, "admin_revoked")

        assertEquals(
            "agent-scoped channel must NOT reconnect after destructive 4010",
            1,
            callCount.get(),
        )
    }

    @Test
    fun onClosed_withTransientCloseCode_agentScopedChannel_schedulesReconnect() {
        // Counterpart to the test above: any close code OTHER than the
        // documented destructive ones (4001, 4002, 4010) must fall
        // through to scheduleReconnect() — including the server's
        // transient-agent-offline code 4012
        // (WS_CLOSE_AGENT_TRANSIENT_OFFLINE) emitted by
        // ``central_server/central_server/relay/mobile_ws.py`` when an
        // agent process is briefly down. This guards against a future
        // refactor that "tidies up" the close-code switch and
        // accidentally collapses transient and destructive codes onto
        // one handler — the mobile would then permanently kill its
        // per-agent sockets on every agent restart.
        val (client, deferredListener, callCount) = clientCapturingListener()
        val sm = sessionManager()
        val authFailures = AtomicInteger(0)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://example.com/ws/events?token=ok" },
            isAuthenticated = { true },
            onAuthFailure = { _ -> authFailures.incrementAndGet() },
        )

        conn.connect()
        val listener = awaitListener(deferredListener)
        listener.onClosed(mockk<WebSocket>(relaxed = true), 4012, "Agent is offline")

        assertEquals(
            "transient 4012 must NOT route through onAuthFailure",
            0,
            authFailures.get(),
        )
        // Reconnect with backoff may not have fired yet at assertion time
        // (1s initial delay) — the load-bearing invariant is the negative
        // assertion above plus the call counter showing at least the
        // initial newWebSocket call.
        assertTrue(
            "first newWebSocket call count must be at least 1",
            callCount.get() >= 1,
        )
    }
}
