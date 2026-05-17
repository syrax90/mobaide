package com.mobaide.app.data.auth

import app.cash.turbine.test
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
 *  - Three-tier auth state (`Unauthenticated`, `UserOnly`, `FullyConnected`)
 *  - Terminal vs transient refresh failure distinction (401 clears
 *    agent tokens → `UserOnly` if user identity exists; IOException / 5xx
 *    keeps tokens).
 *  - Single-flight coalescing of concurrent callers (N parallel
 *    [SessionManager.refresh] calls must produce exactly ONE HTTP
 *    round-trip).
 *  - State transitions: `markUserLoggedIn`, `markAgentConnected`,
 *    `forceLogout`, `agentDisconnect`, `userLogout`.
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
        every { store.getConnectionMode() } returns TokenStore.ConnectionMode.DIRECT
        return store
    }

    private fun stubUserIdentityStore(loggedIn: Boolean = false): UserIdentityStore {
        val store = mockk<UserIdentityStore>(relaxUnitFun = true)
        every { store.isUserLoggedIn() } returns loggedIn
        return store
    }

    // ---- Initial state ------------------------------------------------------

    @Test
    fun initialState_fullyConnected_whenBothStoresHaveTokens() {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        assertTrue(sm.isLoggedIn())
        assertTrue(sm.isUserAuthenticated())
        assertEquals(SessionManager.AuthState.FullyConnected, sm.authState.value)
    }

    @Test
    fun initialState_userOnly_whenOnlyUserStoreHasTokens() {
        val store = stubTokenStore(refresh = null, access = null, agentUrl = null)
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        assertEquals(false, sm.isLoggedIn())
        assertTrue(sm.isUserAuthenticated())
        assertEquals(SessionManager.AuthState.UserOnly, sm.authState.value)
    }

    @Test
    fun initialState_unauthenticated_whenNoTokensExist() {
        val store = stubTokenStore(refresh = null, access = null, agentUrl = null)
        val userStore = stubUserIdentityStore(loggedIn = false)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        assertEquals(false, sm.isLoggedIn())
        assertEquals(false, sm.isUserAuthenticated())
        assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)
    }

    // ---- State transitions --------------------------------------------------

    @Test
    fun markUserLoggedIn_setsUserOnly() {
        val store = stubTokenStore(refresh = null, access = null, agentUrl = null)
        val userStore = stubUserIdentityStore(loggedIn = false)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)
        assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)

        sm.markUserLoggedIn()

        assertEquals(SessionManager.AuthState.UserOnly, sm.authState.value)
        assertTrue(sm.isUserAuthenticated())
        assertEquals(false, sm.isLoggedIn())
    }

    @Test
    fun markAgentConnected_setsFullyConnected() {
        val store = stubTokenStore(refresh = null, access = null, agentUrl = null)
        val userStore = stubUserIdentityStore(loggedIn = false)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        sm.markUserLoggedIn()
        sm.markAgentConnected()

        assertEquals(SessionManager.AuthState.FullyConnected, sm.authState.value)
        assertTrue(sm.isLoggedIn())
        assertTrue(sm.isUserAuthenticated())
    }

    @Test
    fun agentDisconnect_transitionsToUserOnly() {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)
        assertEquals(SessionManager.AuthState.FullyConnected, sm.authState.value)

        sm.agentDisconnect()

        verify(exactly = 1) { store.clear() }
        assertEquals(SessionManager.AuthState.UserOnly, sm.authState.value)
    }

    @Test
    fun userLogout_clearsEverything_andEmitsUnauthenticated() {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        sm.userLogout()

        verify(exactly = 1) { store.clear() }
        verify(exactly = 1) { userStore.clearUserSession() }
        assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)
    }

    @Test
    fun forceLogout_clearsTokenStore_andEmitsUnauthenticated() {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        sm.forceLogout()

        verify(exactly = 1) { store.clear() }
        verify(exactly = 1) { userStore.clearUserSession() }
        assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)
    }

    @Test
    fun forceLogout_isIdempotent() {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        sm.forceLogout()
        sm.forceLogout()
        sm.forceLogout()

        // forceLogout delegates to invalidateUserSession(Manual), which is idempotent:
        // the second and third calls short-circuit because the state is already
        // Unauthenticated after the first call — so store.clear() is called exactly once.
        verify(exactly = 1) { store.clear() }
        assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)
    }

    // ---- Refresh: happy path -----------------------------------------------

    @Test
    fun refresh_onSuccess_savesRotatedTokens_andEmitsFullyConnected() = runTest {
        val store = stubTokenStore(refresh = "r-1", access = "a-1", agentUrl = "https://example.com")
        val userStore = stubUserIdentityStore(loggedIn = true)
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

        val sm = SessionManager(store, userStore, client)

        val result = sm.refresh()

        assertEquals(SessionManager.RefreshResult.Success, result)
        verify(exactly = 1) { store.saveTokens("a-2", "r-2") }
        assertEquals(SessionManager.AuthState.FullyConnected, sm.authState.value)
        assertEquals("https://example.com/api/auth/refresh", reqSlot.captured.url.toString())
        assertEquals("POST", reqSlot.captured.method)
    }

    // ---- Refresh: terminal (401) -------------------------------------------

    @Test
    fun refresh_on401_clearsAgentTokens_andFallsBackToUserOnly() = runTest {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val call = mockk<Call>()
        val reqSlot = slot<Request>()
        every { client.newCall(capture(reqSlot)) } returns call
        every { call.execute() } answers {
            responseFor(reqSlot.captured, 401, """{"detail":"invalid_token"}""")
        }

        val sm = SessionManager(store, userStore, client)

        val result = sm.refresh()

        assertEquals(SessionManager.RefreshResult.Terminal, result)
        verify(exactly = 1) { store.clear() }
        // User identity still exists → fall back to UserOnly, not Unauthenticated
        assertEquals(SessionManager.AuthState.UserOnly, sm.authState.value)
    }

    @Test
    fun refresh_on401_withNoUserIdentity_emitsUnauthenticated() = runTest {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = false)
        val client = mockk<OkHttpClient>()
        val call = mockk<Call>()
        val reqSlot = slot<Request>()
        every { client.newCall(capture(reqSlot)) } returns call
        every { call.execute() } answers {
            responseFor(reqSlot.captured, 401, """{"detail":"invalid_token"}""")
        }

        val sm = SessionManager(store, userStore, client)

        val result = sm.refresh()

        assertEquals(SessionManager.RefreshResult.Terminal, result)
        verify(exactly = 1) { store.clear() }
        assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)
    }

    @Test
    fun refresh_whenRefreshTokenIsMissing_isTerminal_withoutHittingNetwork() = runTest {
        val store = stubTokenStore(refresh = null)
        val userStore = stubUserIdentityStore(loggedIn = true)
        every { store.getRefreshToken() } returns null
        val client = mockk<OkHttpClient>()

        val sm = SessionManager(store, userStore, client)

        val result = sm.refresh()

        assertEquals(SessionManager.RefreshResult.Terminal, result)
        verify(exactly = 0) { client.newCall(any()) }
        verify(exactly = 1) { store.clear() }
        assertEquals(SessionManager.AuthState.UserOnly, sm.authState.value)
    }

    // ---- Refresh: transient (IOException / 5xx) -----------------------------

    @Test
    fun refresh_onIoException_isTransient_andDoesNotClearTokens() = runTest {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val call = mockk<Call>()
        every { client.newCall(any()) } returns call
        every { call.execute() } throws IOException("network down")

        val sm = SessionManager(store, userStore, client)

        val result = sm.refresh()

        assertEquals(SessionManager.RefreshResult.Transient, result)
        verify(exactly = 0) { store.clear() }
        assertEquals(SessionManager.AuthState.FullyConnected, sm.authState.value)
    }

    @Test
    fun refresh_on500_isTransient_andDoesNotClearTokens() = runTest {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val call = mockk<Call>()
        val reqSlot = slot<Request>()
        every { client.newCall(capture(reqSlot)) } returns call
        every { call.execute() } answers {
            responseFor(reqSlot.captured, 500, """{"detail":"internal"}""")
        }

        val sm = SessionManager(store, userStore, client)

        val result = sm.refresh()

        assertEquals(SessionManager.RefreshResult.Transient, result)
        verify(exactly = 0) { store.clear() }
        assertEquals(SessionManager.AuthState.FullyConnected, sm.authState.value)
    }

    // ---- Single-flight coalescing ------------------------------------------

    @Test
    fun refresh_isSingleFlight_concurrentCallersShareOneHttpCall() = runTest {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val call = mockk<Call>()
        val newCallCount = AtomicInteger(0)
        val reqSlot = slot<Request>()
        every { client.newCall(capture(reqSlot)) } answers {
            newCallCount.incrementAndGet()
            call
        }
        every { call.execute() } answers {
            Thread.sleep(20)
            responseFor(
                reqSlot.captured,
                200,
                bodyJson = """{"access_token":"a-new","refresh_token":"r-new"}""",
            )
        }

        val sm = SessionManager(store, userStore, client)

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
        verify(exactly = 1) { store.saveTokens("a-new", "r-new") }
    }

    // ---- Mid-flight forceLogout -------------------------------------------

    @Test
    fun refresh_whenForceLogoutCalledMidFlight_returnsTerminal_withoutThrowing() {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val call = mockk<Call>()
        val releaseExecute = CompletableDeferred<Unit>()
        val reqSlot = slot<Request>()
        every { client.newCall(capture(reqSlot)) } returns call
        every { call.execute() } answers {
            runBlocking { releaseExecute.await() }
            responseFor(
                reqSlot.captured,
                200,
                bodyJson = """{"access_token":"a","refresh_token":"r"}""",
            )
        }

        val sm = SessionManager(store, userStore, client)

        runBlocking {
            val winner = async(Dispatchers.IO) { sm.refresh() }
            withContext(Dispatchers.IO) { Thread.sleep(50) }

            sm.forceLogout()
            releaseExecute.complete(Unit)

            val result = winner.await()
            assertEquals(SessionManager.RefreshResult.Terminal, result)
            assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)
        }
    }

    // ---- invalidateUserSession / sessionEvents -----------------------------

    @Test
    fun invalidateUserSession_tokenRejected_clearsBothStores_andEmitsUnauthenticated() = runTest {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        sm.invalidateUserSession(SessionManager.InvalidationReason.TokenRejected)

        verify(exactly = 1) { store.clear() }
        verify(exactly = 1) { userStore.clearUserSession() }
        assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)
    }

    @Test
    fun invalidateUserSession_tokenRejected_emitsOnSessionEvents() = runTest {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        sm.sessionEvents.test {
            sm.invalidateUserSession(SessionManager.InvalidationReason.TokenRejected)
            val emitted = awaitItem()
            assertEquals(
                SessionManager.InvalidationReason.TokenRejected,
                emitted,
            )
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun invalidateUserSession_accountRemoved_clearsBothStores_andEmitsOnSessionEvents() = runTest {
        // 4010 (WS_CLOSE_ADMIN_REVOKED) on the user-scoped channel routes
        // here. The behaviour mirrors TokenRejected (both stores cleared,
        // state → Unauthenticated, event observable on sessionEvents) but
        // the *reason* is distinct so the login screen can render a
        // user-specific "account removed" message instead of the generic
        // "session expired" copy.
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        sm.sessionEvents.test {
            sm.invalidateUserSession(SessionManager.InvalidationReason.AccountRemoved)
            val emitted = awaitItem()
            assertEquals(
                SessionManager.InvalidationReason.AccountRemoved,
                emitted,
            )
            cancelAndIgnoreRemainingEvents()
        }
        verify(exactly = 1) { store.clear() }
        verify(exactly = 1) { userStore.clearUserSession() }
        assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)
    }

    @Test
    fun invalidateUserSession_manual_doesNotEmitOnSessionEvents() = runTest {
        // Manual logout is user-initiated — no "session expired" toast needed.
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        sm.sessionEvents.test {
            sm.invalidateUserSession(SessionManager.InvalidationReason.Manual)
            expectNoEvents()
        }
        assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)
    }

    @Test
    fun invalidateUserSession_isIdempotent_onSecondCall_doesNotReEmit() = runTest {
        // REST 401 and WS 4001 race on cold start. The second call finds
        // state already Unauthenticated and must short-circuit WITHOUT
        // re-emitting on sessionEvents — otherwise the snackbar fires twice.
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        sm.sessionEvents.test {
            sm.invalidateUserSession(SessionManager.InvalidationReason.TokenRejected)
            assertEquals(
                SessionManager.InvalidationReason.TokenRejected,
                awaitItem(),
            )
            // Second call on an already-Unauthenticated session: no-op.
            sm.invalidateUserSession(SessionManager.InvalidationReason.TokenRejected)
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
        verify(exactly = 1) { store.clear() }
        verify(exactly = 1) { userStore.clearUserSession() }
    }

    @Test
    fun userLogout_delegatesToInvalidateUserSession_withManualReason() = runTest {
        // Regression guard for the refactor: userLogout must still clear
        // both stores and flip to Unauthenticated, and must NOT emit on
        // sessionEvents (Manual reason).
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        sm.sessionEvents.test {
            sm.userLogout()
            expectNoEvents()
        }
        verify(exactly = 1) { store.clear() }
        verify(exactly = 1) { userStore.clearUserSession() }
        assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)
    }

    @Test
    fun markUserLoggedIn_clearsSessionEventsReplayCache() = runTest {
        // The production SessionManager uses `replay = 1` on _sessionEvents
        // so a TokenRejected emitted before LoginViewModel exists (cold
        // start) is still delivered on first collection. Without explicit
        // cache reset on login success, that cached TokenRejected would
        // be re-delivered to the NEXT LoginViewModel instance the next
        // time the user logs out, firing a spurious "session expired"
        // snackbar. This test pins the reset-on-login contract.
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        // 1. Server invalidates — event cached in replay slot.
        sm.invalidateUserSession(SessionManager.InvalidationReason.TokenRejected)

        // 2. User re-logs-in.
        sm.markUserLoggedIn()

        // 3. Fresh subscriber (mirroring a newly-constructed LoginViewModel
        //    at a later logout) must NOT see the stale TokenRejected.
        sm.sessionEvents.test {
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun markAgentConnected_doesNotClearSessionEventsReplayCache() = runTest {
        // Design invariant: only markUserLoggedIn resets the replay cache.
        // markAgentConnected represents an agent-scoped connection event
        // and MUST NOT touch dashboard-JWT session-event state. If a
        // future refactor copy-pastes resetReplayCache() into
        // markAgentConnected (e.g. by pattern-matching on markUserLoggedIn),
        // this test catches it: after invalidate → markAgentConnected,
        // a fresh subscriber should still see the cached TokenRejected
        // (proving the replay slot was not disturbed).
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        // 1. Server invalidates — event cached in replay slot.
        sm.invalidateUserSession(SessionManager.InvalidationReason.TokenRejected)

        // 2. markAgentConnected runs (hypothetically; semantically it would
        //    require UserOnly first, but we are asserting a pure behavioural
        //    contract about the sessionEvents replay cache — that this method
        //    does not call resetReplayCache).
        sm.markAgentConnected()

        // 3. Fresh subscriber still observes the cached TokenRejected —
        //    proving the replay slot was not cleared by markAgentConnected.
        sm.sessionEvents.test {
            assertEquals(
                SessionManager.InvalidationReason.TokenRejected,
                awaitItem(),
            )
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun secondInvalidationAfterReLogin_isDeliveredToFreshSubscriber() = runTest {
        // End-to-end coverage of the full invalidate → re-login → invalidate
        // cycle after the replay-cache reset was introduced. Confirms:
        //   (1) resetReplayCache() in markUserLoggedIn clears stale events,
        //   (2) the event pipeline is NOT broken by the reset — a brand-new
        //       TokenRejected emitted after the reset is still delivered,
        //   (3) a LoginViewModel subscribing AFTER the second invalidation
        //       sees exactly that (second) event, not a stale one.
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
        val client = mockk<OkHttpClient>()
        val sm = SessionManager(store, userStore, client)

        // 1. First server-forced invalidation (cached in replay slot).
        sm.invalidateUserSession(SessionManager.InvalidationReason.TokenRejected)
        assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)

        // 2. User re-logs-in: replay cache cleared, state → UserOnly.
        sm.markUserLoggedIn()
        assertEquals(SessionManager.AuthState.UserOnly, sm.authState.value)

        // 3. Second server-forced invalidation (e.g. the new JWT expires or
        //    JWT_SECRET_KEY rotates again): must re-populate the replay slot.
        sm.invalidateUserSession(SessionManager.InvalidationReason.TokenRejected)
        assertEquals(SessionManager.AuthState.Unauthenticated, sm.authState.value)

        // 4. Fresh LoginViewModel subscribes AFTER the second invalidation
        //    and must see the (new, not stale) TokenRejected.
        sm.sessionEvents.test {
            assertEquals(
                SessionManager.InvalidationReason.TokenRejected,
                awaitItem(),
            )
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- Refresh body validation -------------------------------------------

    @Test
    fun refresh_requestBody_containsRefreshTokenAsJson() = runTest {
        val store = stubTokenStore()
        val userStore = stubUserIdentityStore(loggedIn = true)
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

        val sm = SessionManager(store, userStore, client)
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
