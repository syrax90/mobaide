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
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * Cover the new ``onIncompatibleServer`` callback wired through
 * [WebSocketConnection]. Mirrors the auth-failure tests but for close code
 * ``4002`` (PROTOCOL_INCOMPATIBLE).
 */
class WebSocketConnectionIncompatibleTest {

    private fun clientCapturingListener(): Pair<OkHttpClient, CompletableDeferred<WebSocketListener>> {
        val client = mockk<OkHttpClient>()
        val deferred = CompletableDeferred<WebSocketListener>()
        val slotListener = slot<WebSocketListener>()
        every { client.newWebSocket(any(), capture(slotListener)) } answers {
            if (!deferred.isCompleted) deferred.complete(slotListener.captured)
            mockk<WebSocket>(relaxed = true)
        }
        return client to deferred
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
                error("WebSocketListener was never captured")
            }
        }

    @Test
    fun onClosed_withCode4002_invokesOnIncompatibleServer() {
        val (client, deferred) = clientCapturingListener()
        val invocations = AtomicInteger(0)
        val capturedReason = AtomicReference<String>()
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sessionManager(),
            urlProvider = { "wss://central.example.com/ws/user-events?token=v0" },
            isAuthenticated = { true },
            onIncompatibleServer = { reason ->
                invocations.incrementAndGet()
                capturedReason.set(reason)
            },
        )

        conn.connect()
        val listener = awaitListener(deferred)
        listener.onClosed(mockk<WebSocket>(relaxed = true), 4002, "Server requires v2")

        assertEquals(1, invocations.get())
        assertEquals("Server requires v2", capturedReason.get())
    }

    @Test
    fun onClosed_withCode4001_doesNotInvokeOnIncompatibleServer() {
        val (client, deferred) = clientCapturingListener()
        val incompatibleInvocations = AtomicInteger(0)
        val authFailures = AtomicInteger(0)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sessionManager(),
            urlProvider = { "wss://central.example.com/ws/user-events?token=v0" },
            isAuthenticated = { true },
            onAuthFailure = { authFailures.incrementAndGet() },
            onIncompatibleServer = { incompatibleInvocations.incrementAndGet() },
        )

        conn.connect()
        val listener = awaitListener(deferred)
        listener.onClosed(mockk<WebSocket>(relaxed = true), 4001, "Token rejected")

        assertEquals(1, authFailures.get())
        assertEquals(0, incompatibleInvocations.get())
    }
}
