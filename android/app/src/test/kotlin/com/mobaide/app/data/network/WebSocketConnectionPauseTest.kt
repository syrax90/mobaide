package com.mobaide.app.data.network

import com.mobaide.app.data.auth.SessionManager
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * [WebSocketConnection.pause] — the non-destructive stop the unwatched-session
 * governor uses.
 *
 * [OkHttpClient] is mocked so the test can count sockets the production code
 * builds, mirroring `WebSocketConnectionForceReconnectTest`.
 */
class WebSocketConnectionPauseTest {

    private fun clientCountingSockets(): Pair<OkHttpClient, AtomicInteger> {
        val client = mockk<OkHttpClient>()
        val calls = AtomicInteger(0)
        val captured = slot<WebSocketListener>()
        every { client.newWebSocket(any(), capture(captured)) } answers {
            calls.incrementAndGet()
            mockk<WebSocket>(relaxed = true)
        }
        return client to calls
    }

    private fun sessionManager(): SessionManager =
        mockk<SessionManager>(relaxUnitFun = true).also {
            every { it.isLoggedIn() } returns true
        }

    private fun awaitCallCount(counter: AtomicInteger, target: Int) {
        runBlocking {
            withTimeout(2_000) {
                while (counter.get() < target) delay(10)
            }
        }
    }

    @Test
    fun connectAfterPause_opensASocketAgain() {
        // The contract that makes `pause()` worth having over `disconnect()`,
        // which ends the connection's scope and turns every later `connect()`
        // into a silent no-op.
        val (client, calls) = clientCountingSockets()
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sessionManager(),
            urlProvider = { "wss://example.com/ws/session/abc?token=ok" },
            isAuthenticated = { true },
        )

        conn.connect()
        awaitCallCount(calls, 1)

        conn.pause()
        conn.connect()

        awaitCallCount(calls, 2)
        assertEquals(2, calls.get())
    }

    @Test
    fun pauseDuringSocketCreation_closesTheSocketItRacedWith() {
        // The mirror of the case below: the recheck before creation cannot cover
        // a pause that lands *across* `newWebSocket`, because OkHttp has already
        // started the socket by then and the field assignment has not happened.
        // Without the post-publication recheck that socket survives with a
        // listener deaf on its stale generation, and nothing ever closes it.
        val client = mockk<OkHttpClient>()
        val raced = mockk<WebSocket>(relaxed = true)
        lateinit var conn: WebSocketConnection
        every { client.newWebSocket(any(), any()) } answers {
            conn.pause()
            raced
        }
        conn = WebSocketConnection(
            client = client,
            sessionManager = sessionManager(),
            urlProvider = { "wss://example.com/ws/session/abc?token=ok" },
            isAuthenticated = { true },
        )

        conn.connect()

        verify(timeout = 2_000) { raced.close(1000, any()) }
    }

    @Test
    fun pauseDuringASlowUrlResolution_buildsNoSocket() {
        // `urlProvider` can block on a token refresh, and `pause()` is
        // synchronous — it is the one `generation++` that does not hold the
        // mutex. Without the post-resolution `shouldReconnect` recheck the
        // parked attempt wakes, captures the *new* generation, and passes its
        // own staleness guard: a live socket streaming a full ring replay while
        // the caller believes the session is suspended, orphaned as soon as the
        // next resume overwrites the field.
        val (client, calls) = clientCountingSockets()
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val conn = WebSocketConnection(
            client = client,
            sessionManager = sessionManager(),
            urlProvider = {
                entered.countDown()
                release.await(2, TimeUnit.SECONDS)
                "wss://example.com/ws/session/abc?token=ok"
            },
            isAuthenticated = { true },
        )

        conn.connect()
        assertTrue("doConnect never reached the URL provider", entered.await(2, TimeUnit.SECONDS))

        conn.pause()
        release.countDown()

        // Long enough for the released attempt to run to completion.
        runBlocking { delay(200) }
        assertEquals(0, calls.get())
    }
}
