package com.claudemobile.app.data.auth

import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.util.concurrent.atomic.AtomicInteger

/**
 * Unit tests for [SessionManager] — covers:
 *
 *  - Terminal vs transient refresh failure distinction (401 clears
 *    tokens + emits `LoggedOut`; IOException / 5xx keeps tokens).
 *  - Single-flight coalescing of concurrent callers (N parallel
 *    [SessionManager.refresh] calls must produce exactly ONE HTTP
 *    round-trip, matching the design fix for the rapid double-refresh
 *    regression).
 *  - State transitions: `markLoggedIn`, `forceLogout`, initial state
 *    from [TokenStore.isLoggedIn].
 *
 * Follows the same no-MockWebServer pattern as [RestApiTest]: mock
 * [OkHttpClient] + [Call] + [Response] directly through MockK.
 */
class SessionManagerTest {

    // ---- Helpers ------------------------------------------------------------

    private val jsonType = "application/json".toMediaType()

    private fun responseFor(
        req: Request,
        code: Int,
        bodyJson: String,
    ): Response = Response.Builder()
        .request(req)
        .protocol(Protocol.HTTP_1_1)
        .code(code)
        .message(if (code in 200..299) "OK" else "ERR")
        .body(bodyJson.toResponseBody(jsonType))
        .build()

    private fun stubTokenStore(
        refresh: String? = "refresh-initial",
        access: String? = "access-initial",
        agentUrl: String? = "https://example.com",
    ): TokenStore {
        val store = mockk<TokenStore>(relaxUnitFun = true)
        every { store.getRefreshToken() } returns refresh
        every { store.getAccessToken() } returns access
        every { store.getAgentUrl() } returns agentUrl
        every { store.isLoggedIn() } returns (access != null && refresh != null && agentUrl != null)
        return store
    }

    // ---- Initial state ------------------------------------------------------

    @Test
    fun initialState_reflectsTokenStoreIsLoggedIn_whenTrue() {
        val store = stubTokenStore()
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, client)

        assertTrue(sm.isLoggedIn())
        assertEquals(SessionManager.AuthState.LoggedIn, sm.authState.value)
    }

    @Test
    fun initialState_reflectsTokenStoreIsLoggedIn_whenFalse() {
        val store = stubTokenStore(refresh = null, access = null, agentUrl = null)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, client)

        assertEquals(false, sm.isLoggedIn())
        assertEquals(SessionManager.AuthState.LoggedOut, sm.authState.value)
    }

    // ---- markLoggedIn / forceLogout -----------------------------------------

    @Test
    fun markLoggedIn_flipsStateToLoggedIn() {
        val store = stubTokenStore(refresh = null, access = null, agentUrl = null)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, client)
        assertEquals(SessionManager.AuthState.LoggedOut, sm.authState.value)

        sm.markLoggedIn()

        assertEquals(SessionManager.AuthState.LoggedIn, sm.authState.value)
    }

    @Test
    fun forceLogout_clearsTokenStore_andEmitsLoggedOut() {
        val store = stubTokenStore()
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, client)

        sm.forceLogout()

        verify(exactly = 1) { store.clear() }
        assertEquals(SessionManager.AuthState.LoggedOut, sm.authState.value)
    }

    @Test
    fun forceLogout_isIdempotent() {
        val store = stubTokenStore()
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, client)

        sm.forceLogout()
        sm.forceLogout()
        sm.forceLogout()

        // Three calls, three clear()s — all on an already-cleared store,
        // each one a no-op from the user's perspective. The important
        // property is that none of them throw.
        verify(exactly = 3) { store.clear() }
        assertEquals(SessionManager.AuthState.LoggedOut, sm.authState.value)
    }

    // ---- Refresh: happy path -----------------------------------------------

    @Test
    fun refresh_onSuccess_savesRotatedTokens_andEmitsLoggedIn() = runTest {
        // Start LoggedIn (access + refresh present) so the applyRefreshSuccess
        // guard — which correctly bails if forceLogout() won the race — does not
        // mistake "initially not yet logged in" for "logout already won".
        val store = stubTokenStore(refresh = "r-1", access = "a-1", agentUrl = "https://example.com")
        // Re-stub just the bits refresh() touches (already covered by stubTokenStore above,
        // but kept explicit for clarity):
        every { store.getRefreshToken() } returns "r-1"
        every { store.getAgentUrl() } returns "https://example.com"

        val client = mockk<OkHttpClient>()
        val reqSlot = slot<Request>()
        val call = mockk<Call>()
        every { client.newCall(capture(reqSlot)) } returns call
        every { call.execute() } answers {
            responseFor(
                reqSlot.captured,
                200,
                bodyJson = """{"access_token":"a-2","refresh_token":"r-2"}""",
            )
        }

        val sm = SessionManager(store, client)

        val result = sm.refresh()

        assertEquals(SessionManager.RefreshResult.Success, result)
        verify(exactly = 1) { store.saveTokens("a-2", "r-2") }
        assertEquals(SessionManager.AuthState.LoggedIn, sm.authState.value)
        // Request body must be valid JSON, and MUST carry the stored refresh token.
        assertEquals("https://example.com/api/auth/refresh", reqSlot.captured.url.toString())
        assertEquals("POST", reqSlot.captured.method)
    }

    // ---- Refresh: terminal (401) -------------------------------------------

    @Test
    fun refresh_on401_clearsTokens_andEmitsLoggedOut() = runTest {
        val store = stubTokenStore()
        val client = mockk<OkHttpClient>()
        val call = mockk<Call>()
        val reqSlot = slot<Request>()
        every { client.newCall(capture(reqSlot)) } returns call
        every { call.execute() } answers {
            responseFor(reqSlot.captured, 401, """{"detail":"invalid_token"}""")
        }

        val sm = SessionManager(store, client)

        val result = sm.refresh()

        assertEquals(SessionManager.RefreshResult.Terminal, result)
        verify(exactly = 1) { store.clear() }
        assertEquals(SessionManager.AuthState.LoggedOut, sm.authState.value)
    }

    @Test
    fun refresh_whenRefreshTokenIsMissing_isTerminal_withoutHittingNetwork() = runTest {
        val store = stubTokenStore(refresh = null)
        every { store.getRefreshToken() } returns null
        val client = mockk<OkHttpClient>()

        val sm = SessionManager(store, client)

        val result = sm.refresh()

        assertEquals(SessionManager.RefreshResult.Terminal, result)
        verify(exactly = 0) { client.newCall(any()) }
        verify(exactly = 1) { store.clear() }
        assertEquals(SessionManager.AuthState.LoggedOut, sm.authState.value)
    }

    // ---- Refresh: transient (IOException / 5xx) -----------------------------

    @Test
    fun refresh_onIoException_isTransient_andDoesNotClearTokens() = runTest {
        val store = stubTokenStore()
        val client = mockk<OkHttpClient>()
        val call = mockk<Call>()
        every { client.newCall(any()) } returns call
        every { call.execute() } throws IOException("network down")

        val sm = SessionManager(store, client)

        val result = sm.refresh()

        assertEquals(SessionManager.RefreshResult.Transient, result)
        verify(exactly = 0) { store.clear() }
        assertEquals(SessionManager.AuthState.LoggedIn, sm.authState.value)
    }

    @Test
    fun refresh_on500_isTransient_andDoesNotClearTokens() = runTest {
        val store = stubTokenStore()
        val client = mockk<OkHttpClient>()
        val call = mockk<Call>()
        val reqSlot = slot<Request>()
        every { client.newCall(capture(reqSlot)) } returns call
        every { call.execute() } answers {
            responseFor(reqSlot.captured, 500, """{"detail":"internal"}""")
        }

        val sm = SessionManager(store, client)

        val result = sm.refresh()

        assertEquals(SessionManager.RefreshResult.Transient, result)
        verify(exactly = 0) { store.clear() }
        assertEquals(SessionManager.AuthState.LoggedIn, sm.authState.value)
    }

    // ---- Single-flight coalescing ------------------------------------------

    @Test
    fun refresh_isSingleFlight_concurrentCallersShareOneHttpCall() = runTest {
        val store = stubTokenStore()
        val client = mockk<OkHttpClient>()
        val call = mockk<Call>()
        val newCallCount = AtomicInteger(0)
        val reqSlot = slot<Request>()
        every { client.newCall(capture(reqSlot)) } answers {
            newCallCount.incrementAndGet()
            call
        }
        every { call.execute() } answers {
            // Tiny blocking sleep so concurrent callers actually overlap.
            Thread.sleep(20)
            responseFor(
                reqSlot.captured,
                200,
                bodyJson = """{"access_token":"a-new","refresh_token":"r-new"}""",
            )
        }

        val sm = SessionManager(store, client)

        // Fire 10 concurrent refresh calls. All must resolve to Success,
        // but only ONE underlying HTTP round-trip is allowed.
        val results = (1..10)
            .map { async { sm.refresh() } }
            .awaitAll()

        assertEquals(10, results.size)
        assertTrue(
            "all callers must see Success",
            results.all { it == SessionManager.RefreshResult.Success },
        )
        assertEquals(
            "single-flight should coalesce to exactly one HTTP call",
            1,
            newCallCount.get(),
        )
        // Tokens rotated exactly once.
        verify(exactly = 1) { store.saveTokens("a-new", "r-new") }
    }

    // ---- Mid-flight forceLogout -------------------------------------------

    @Test
    fun refresh_whenForceLogoutCalledMidFlight_returnsTerminal_withoutThrowing() {
        // Regression guard (validator finding WARN-02): if `forceLogout()`
        // is called while a refresh is in flight (e.g. user taps Logout
        // while the interceptor is awaiting its own Deferred),
        // `forceLogout` cancels `inFlight.getAndSet(null)`, which is the
        // very Deferred the winner is awaiting. The winner's `await()`
        // therefore throws `JobCancellationException` and, if unhandled,
        // escapes out of `runBlocking { sessionManager.refresh() }` in
        // the OkHttp interceptor, surfacing as a misleading network-error
        // toast instead of a clean "session expired" flow.
        //
        // The fix wraps the winner's `fresh.await()` in the same
        // `catch (_: Exception)` that the follower path already has, so
        // the cancellation is converted to `RefreshResult.Terminal`.
        //
        // Uses `runBlocking`, not `runTest`, so that the two coroutines
        // actually run in parallel on real threads — `runTest` would
        // serialize them on a single virtual-time dispatcher and the
        // race would never materialise.
        val store = stubTokenStore()
        val client = mockk<OkHttpClient>()
        val call = mockk<Call>()
        // Gate the refresh response on a CompletableDeferred so we can
        // deterministically interleave the cancel-while-awaiting.
        val releaseExecute = CompletableDeferred<Unit>()
        val reqSlot = slot<Request>()
        every { client.newCall(capture(reqSlot)) } returns call
        every { call.execute() } answers {
            runBlocking { releaseExecute.await() }
            // We never reach this — the Deferred gets cancelled first.
            responseFor(
                reqSlot.captured,
                200,
                bodyJson = """{"access_token":"a","refresh_token":"r"}""",
            )
        }

        val sm = SessionManager(store, client)

        runBlocking {
            // Launch the winner on a real IO thread.
            val winner = async(Dispatchers.IO) { sm.refresh() }

            // Give the winner a beat to enter `fresh.await()`.
            withContext(Dispatchers.IO) { Thread.sleep(50) }

            // Mid-flight logout — cancels the in-flight Deferred from
            // outside, mirroring what AuthRepository.logout → SessionManager.forceLogout
            // does on a user tap.
            sm.forceLogout()

            // Release the (now-cancelled) HTTP call so the mock thread
            // doesn't leak. This write is only observed if the Deferred
            // wasn't cancelled — in that case the test would hang until
            // the runBlocking times out, which is the correct failure
            // signal for the regression we're guarding against.
            releaseExecute.complete(Unit)

            val result = winner.await()
            assertEquals(SessionManager.RefreshResult.Terminal, result)
            assertEquals(SessionManager.AuthState.LoggedOut, sm.authState.value)
        }
    }

    // ---- Refresh body validation -------------------------------------------

    @Test
    fun refresh_requestBody_containsRefreshTokenAsJson() = runTest {
        val store = stubTokenStore()
        every { store.getRefreshToken() } returns "r-sentinel"
        val client = mockk<OkHttpClient>()
        val call = mockk<Call>()
        val reqSlot = slot<Request>()
        every { client.newCall(capture(reqSlot)) } returns call
        every { call.execute() } answers {
            responseFor(
                reqSlot.captured,
                200,
                bodyJson = """{"access_token":"a","refresh_token":"r"}""",
            )
        }

        val sm = SessionManager(store, client)
        sm.refresh()

        val buffer = okio.Buffer()
        reqSlot.captured.body?.writeTo(buffer)
        val body = buffer.readUtf8()
        assertNotNull(body)
        assertTrue(
            "body must be JSON-shaped with the stored refresh token",
            body.contains("\"refresh_token\"") && body.contains("r-sentinel"),
        )
    }
}
