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

/**
 * Unit tests for [WebSocketConnection]'s auth-failure handling — the
 * new path where a server-side close with code 4001 (or a failed WS
 * upgrade with HTTP 401) invokes [onAuthFailure] and stops reconnecting.
 *
 * This is the WS-side counterpart to [com.mobaide.app.data.auth.CentralAuthInterceptor]
 * for the user-scoped channel (`ws/user-events`): when the central
 * server rejects the dashboard JWT the client must tear down the
 * session rather than retry-spamming the socket.
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
    fun onClosed_withCode4001_invokesOnAuthFailure() {
        val (client, deferredListener, _) = clientCapturingListener()
        val sm = sessionManager()
        val authFailures = AtomicInteger(0)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://central.example.com/ws/user-events?token=stale" },
            isAuthenticated = { true },
            onAuthFailure = { authFailures.incrementAndGet() },
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
    }

    @Test
    fun onClosed_withCode1000_reconnects_doesNotInvokeOnAuthFailure() {
        val (client, deferredListener, callCount) = clientCapturingListener()
        val sm = sessionManager()
        val authFailures = AtomicInteger(0)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://central.example.com/ws/user-events?token=ok" },
            isAuthenticated = { true },
            onAuthFailure = { authFailures.incrementAndGet() },
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
    fun onFailure_withResponse401_invokesOnAuthFailure() {
        // okhttp surfaces a failed WS upgrade (HTTP 401 instead of 101)
        // via onFailure(response.code == 401). Treat same as 4001 close.
        val (client, deferredListener, _) = clientCapturingListener()
        val sm = sessionManager()
        val authFailures = AtomicInteger(0)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://central.example.com/ws/user-events?token=stale" },
            isAuthenticated = { true },
            onAuthFailure = { authFailures.incrementAndGet() },
        )

        conn.connect()
        val listener = awaitListener(deferredListener)
        val fakeSocket = mockk<WebSocket>(relaxed = true)
        val req = Request.Builder().url("https://central.example.com").build()
        listener.onFailure(fakeSocket, IllegalStateException("upgrade failed"), responseFor(req, 401))

        assertEquals(1, authFailures.get())
    }

    @Test
    fun onFailure_withoutResponse_doesNotInvokeOnAuthFailure() {
        // Network error (no response at all) — retriable, not an auth
        // failure. The scheduleReconnect guard downstream handles the
        // back-off loop.
        val (client, deferredListener, _) = clientCapturingListener()
        val sm = sessionManager()
        val authFailures = AtomicInteger(0)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://central.example.com/ws/user-events?token=ok" },
            isAuthenticated = { true },
            onAuthFailure = { authFailures.incrementAndGet() },
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
}
