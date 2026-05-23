package com.mobaide.app.data.network

import com.mobaide.app.data.auth.SessionManager
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger

/**
 * Unit tests for [WebSocketConnection.forceReconnect] — the entry point
 * used by the terminal-screen lifecycle hook to wake the session WS up
 * when the device screen turns back on.  Two properties matter:
 *
 *  1. **Backoff is reset and a fresh socket is opened immediately.**
 *     Without this the user can sit on the "Reconnecting…" overlay for
 *     up to 30 s — the cap of [WebSocketConnection]'s exponential
 *     backoff after a Doze-stalled retry chain.
 *
 *  2. **Stale listener callbacks from the closed socket cannot inject
 *     phantom events into the new one.**  OkHttp delivers the previous
 *     socket's `onClosed` / `onFailure` asynchronously after
 *     `webSocket.close(...)`; if the listener weren't gated on the
 *     `generation` counter the UI would flicker `Live → Reconnecting →
 *     Live` every time the user unlocks the phone.
 *
 * [OkHttpClient] is mocked so we can capture each [WebSocketListener]
 * the production code installs and drive its callbacks directly.  This
 * mirrors the pattern in `WebSocketConnectionAuthFailureTest`.
 */
class WebSocketConnectionForceReconnectTest {

    private fun responseFor(req: Request, code: Int): Response =
        Response.Builder()
            .request(req)
            .protocol(Protocol.HTTP_1_1)
            .code(code)
            .message(if (code in 200..299) "OK" else "ERR")
            .build()

    /**
     * Build a mock OkHttpClient whose every `newWebSocket` call drops the
     * captured listener onto an unbounded [Channel], so the test can wait
     * for the N-th listener in the order they were installed.  Each call
     * also returns a fresh mock [WebSocket] so the closes the production
     * code issues hit the right object without throwing.
     */
    private fun clientCapturingListeners(): Triple<
        OkHttpClient,
        Channel<WebSocketListener>,
        AtomicInteger, // newWebSocket call count
    > {
        val client = mockk<OkHttpClient>()
        val channel = Channel<WebSocketListener>(capacity = Channel.UNLIMITED)
        val calls = AtomicInteger(0)
        val slotListener = slot<WebSocketListener>()
        every { client.newWebSocket(any(), capture(slotListener)) } answers {
            calls.incrementAndGet()
            channel.trySend(slotListener.captured)
            mockk<WebSocket>(relaxed = true)
        }
        return Triple(client, channel, calls)
    }

    private fun sessionManager(): SessionManager =
        mockk<SessionManager>(relaxUnitFun = true).also {
            every { it.isLoggedIn() } returns true
        }

    private fun awaitListener(channel: Channel<WebSocketListener>): WebSocketListener =
        runBlocking {
            try {
                withTimeout(2_000) { channel.receive() }
            } catch (_: TimeoutCancellationException) {
                error("WebSocketListener was never captured — doConnect did not run")
            }
        }

    private fun awaitCallCount(counter: AtomicInteger, target: Int) {
        runBlocking {
            withTimeout(2_000) {
                while (counter.get() < target) delay(10)
            }
        }
    }

    /** Drain any extra coroutines so an immediately-following negative
     *  assertion (`assertEquals(1, counter.get())`) isn't a race against
     *  late dispatched work. */
    private fun letScopeSettle() {
        runBlocking { delay(75) }
    }

    @Test
    fun forceReconnect_opensASecondSocket_immediately() {
        // The load-bearing invariant for the bug fix: after a force-
        // reconnect the OkHttpClient must see a *new* newWebSocket() call
        // without waiting on the exponential backoff that the previous
        // (failed) connection chain may have accumulated.
        val (client, listeners, callCount) = clientCapturingListeners()
        val sm = sessionManager()
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://example.com/ws/session/abc?token=ok" },
            isAuthenticated = { true },
        )

        conn.connect()
        val firstListener = awaitListener(listeners)
        firstListener.onOpen(mockk<WebSocket>(relaxed = true), responseFor(
            Request.Builder().url("https://example.com").build(), 101,
        ))

        conn.forceReconnect()

        awaitCallCount(callCount, 2)
        assertEquals(2, callCount.get())
    }

    @Test
    fun forceReconnect_staleOnClosed_doesNotEmitPhantomDisconnected() {
        // The generation guard makes the previous socket's listener inert
        // the moment forceReconnect bumps the counter — so the late
        // `onClosed` OkHttp may deliver from the just-closed socket
        // cannot flip the ViewModel's state machine back to Reconnecting
        // after the new socket has already announced Connected.
        val (client, listeners, callCount) = clientCapturingListeners()
        val sm = sessionManager()
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://example.com/ws/session/abc?token=ok" },
            isAuthenticated = { true },
        )

        // Subscribe to the events flow BEFORE connect() so no event is
        // missed.  The SharedFlow has replay=1 + buffer=100 so we can
        // simply read all events in order at the end.
        val seen = mutableListOf<WsEvent>()
        val collectorScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        collectorScope.launch {
            conn.events.collect { seen.add(it) }
        }

        conn.connect()
        val firstListener = awaitListener(listeners)
        val firstSocket = mockk<WebSocket>(relaxed = true)
        firstListener.onOpen(firstSocket, responseFor(
            Request.Builder().url("https://example.com").build(), 101,
        ))

        conn.forceReconnect()
        awaitCallCount(callCount, 2)
        val secondListener = awaitListener(listeners)
        secondListener.onOpen(mockk<WebSocket>(relaxed = true), responseFor(
            Request.Builder().url("https://example.com").build(), 101,
        ))

        // Now simulate OkHttp finally delivering the close event for the
        // FIRST socket — this would happen IRL after our `webSocket.close`
        // call inside forceReconnect propagates through the OkHttp dispatcher.
        firstListener.onClosed(firstSocket, 1000, "stale close")

        // Give the (no-op) callback a moment to run — generation guard
        // should short-circuit before any state mutation.
        letScopeSettle()
        collectorScope.cancel()

        // Exactly two Connected events should have made it through (one
        // per generation); zero Disconnected events from the stale close.
        val connectedCount = seen.count { it is WsEvent.Connected }
        val disconnectedCount = seen.count { it is WsEvent.Disconnected }
        assertEquals("two opens → two Connected", 2, connectedCount)
        assertEquals(
            "stale onClosed from gen-0 listener must be gated by generation guard",
            0,
            disconnectedCount,
        )
    }

    @Test
    fun forceReconnect_isNoOp_whenShouldReconnectFalse() {
        // After a 4001/4010 close [WebSocketConnection.handleAuthFailure]
        // sets shouldReconnect = false to permanently suppress retries —
        // re-issuing a fresh token cannot recover a deleted account.  A
        // lifecycle nudge after that must respect the suppression and
        // NOT spawn another newWebSocket call.
        val (client, listeners, callCount) = clientCapturingListeners()
        val sm = sessionManager()
        val authFailures = AtomicInteger(0)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://example.com/ws/user-events?token=stale" },
            isAuthenticated = { true },
            onAuthFailure = { authFailures.incrementAndGet() },
        )

        conn.connect()
        val listener = awaitListener(listeners)
        listener.onClosed(mockk<WebSocket>(relaxed = true), 4001, "Invalid token")
        assertEquals("4001 hit the auth-failure path", 1, authFailures.get())

        conn.forceReconnect()
        letScopeSettle()

        // Exactly one newWebSocket call total — forceReconnect was a no-op
        // because shouldReconnect = false after the 4001.
        assertEquals(
            "forceReconnect after permanent suppression must not reopen the socket",
            1,
            callCount.get(),
        )
    }

    @Test
    fun forceReconnect_isNoOp_whenNotAuthenticated() {
        // Mirror of the above for the user-scoped channel's auth predicate.
        // If the caller is logged out at the moment of the lifecycle nudge,
        // forceReconnect must not race against a still-tearing-down session.
        val (client, _, callCount) = clientCapturingListeners()
        val sm = sessionManager()
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://example.com/ws/user-events?token=ok" },
            isAuthenticated = { false },
        )

        conn.forceReconnect()
        letScopeSettle()

        assertEquals(
            "forceReconnect when isAuthenticated() == false must not reopen the socket",
            0,
            callCount.get(),
        )
    }

    @Test
    fun forceReconnect_resetsBackoff_afterAFailedAttempt() {
        // White-box property: the previous failed attempt may have driven
        // the backoff up to 30 s before the lifecycle nudge arrives.  Force-
        // reconnect must reset it so the *next* failure starts at 1 s
        // again, otherwise the user's nudge buys nothing on networks that
        // also fail the first post-resume attempt.  Verified indirectly:
        // we observe that after forceReconnect + onFailure, the next
        // newWebSocket call happens well under the 30 s ceiling — within
        // the [INITIAL_BACKOFF_MS = 1000] window plus a slack.
        val (client, listeners, callCount) = clientCapturingListeners()
        val sm = sessionManager()
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://example.com/ws/session/abc?token=ok" },
            isAuthenticated = { true },
        )

        conn.connect()
        val l1 = awaitListener(listeners)

        // Drive a failure to ramp the backoff (1s → 2s).
        l1.onFailure(
            mockk<WebSocket>(relaxed = true),
            java.io.IOException("first fail"),
            response = null,
        )
        // Wait until the scheduleReconnect timer fires the next doConnect.
        awaitCallCount(callCount, 2)
        val l2 = awaitListener(listeners)
        // Second failure — backoff would now climb again toward 4s.
        l2.onFailure(
            mockk<WebSocket>(relaxed = true),
            java.io.IOException("second fail"),
            response = null,
        )

        // Lifecycle nudge: this should cancel the pending scheduleReconnect
        // and immediately fire a new doConnect — no backoff wait.
        val countBeforeForce = callCount.get()
        conn.forceReconnect()
        awaitCallCount(callCount, countBeforeForce + 1)

        assertTrue(
            "forceReconnect produced at least one immediate newWebSocket call",
            callCount.get() > countBeforeForce,
        )
    }

    @Test
    fun connect_initialListener_alsoGuardsAgainstStaleCallbacks_afterForceReconnect() {
        // Regression guard for the per-listener `myGeneration` capture: if
        // a future refactor moved the capture out of the closure, the
        // very first listener would lose its generation tag and the stale-
        // callback test above could pass for the wrong reason.  Verify
        // the first listener's onClosed is silently ignored once
        // forceReconnect has bumped the counter — but its sibling
        // SECOND listener's onClosed still emits.
        val (client, listeners, callCount) = clientCapturingListeners()
        val sm = sessionManager()
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sm,
            urlProvider = { "wss://example.com/ws/session/abc?token=ok" },
            isAuthenticated = { true },
        )
        val seen = mutableListOf<WsEvent>()
        val collectorScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        collectorScope.launch {
            conn.events.collect { seen.add(it) }
        }

        conn.connect()
        val l1 = awaitListener(listeners)
        l1.onOpen(mockk<WebSocket>(relaxed = true), responseFor(
            Request.Builder().url("https://example.com").build(), 101,
        ))

        conn.forceReconnect()
        awaitCallCount(callCount, 2)
        val l2 = awaitListener(listeners)
        l2.onOpen(mockk<WebSocket>(relaxed = true), responseFor(
            Request.Builder().url("https://example.com").build(), 101,
        ))

        // Stale: must be ignored.
        l1.onClosed(mockk<WebSocket>(relaxed = true), 1000, "stale")
        // Current: must emit Disconnected.
        l2.onClosed(mockk<WebSocket>(relaxed = true), 1000, "current")

        letScopeSettle()
        collectorScope.cancel()

        val disconnectedCount = seen.count { it is WsEvent.Disconnected }
        assertEquals(
            "only the current-generation listener's onClosed emits Disconnected",
            1,
            disconnectedCount,
        )
        assertNull(
            "no Error event from stale callbacks",
            seen.firstOrNull { it is WsEvent.Error },
        )
    }
}
