package com.claudemobile.app.data.auth

import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [AuthInterceptor] — exercise the request-path decisions
 * (skip list, synthesised-401 short-circuit when logged out, expiring-soon
 * pre-refresh, Bearer injection) and the 401 → refresh → replay retry
 * path. [SessionManager], [TokenStore], and [Interceptor.Chain] are
 * mocked with MockK; [Request] / [Response] are built with the real
 * OkHttp builders to keep the header/URL/code semantics realistic.
 *
 * Crucially, these tests pin that the interceptor never issues MORE THAN
 * ONE refresh per `intercept()` call — the regression we fixed was a
 * double-refresh (once from the pre-emptive expiring-soon branch, once
 * from the 401-retry branch) that produced the rapid pair of
 * `POST /api/auth/refresh` requests seen in the original crash logs.
 */
class AuthInterceptorTest {

    // ---- Helpers ------------------------------------------------------------

    private fun request(url: String): Request =
        Request.Builder().url(url).get().build()

    private fun responseFor(
        req: Request,
        code: Int,
        bodyJson: String = "{}",
    ): Response = Response.Builder()
        .request(req)
        .protocol(Protocol.HTTP_1_1)
        .code(code)
        .message(if (code in 200..299) "OK" else "ERR")
        .body(bodyJson.toResponseBody("application/json".toMediaType()))
        .build()

    /** Build a SessionManager mock whose [SessionManager.isLoggedIn] is stubbed. */
    private fun newSessionManager(loggedIn: Boolean = true): SessionManager {
        val sm = mockk<SessionManager>(relaxUnitFun = true)
        every { sm.isLoggedIn() } returns loggedIn
        return sm
    }

    // ---- Skip-list paths ----------------------------------------------------

    @Test
    fun intercept_skipsAuthHeader_forTokenEndpoint() {
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/auth/token")
        every { chain.request() } returns original
        every { chain.proceed(any()) } answers {
            val forwarded = firstArg<Request>()
            assertSame("skip-path must not rewrite the request", original, forwarded)
            assertNull(
                "skip-path must not attach a Bearer header",
                forwarded.header("Authorization"),
            )
            responseFor(forwarded, 200)
        }

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        assertEquals(200, result.code)
        // No token-store or session-manager interaction at all on the skip path.
        verify(exactly = 0) { tokenStore.isAccessTokenExpiringSoon() }
        verify(exactly = 0) { tokenStore.getAccessToken() }
        verify(exactly = 0) { sessionManager.isLoggedIn() }
    }

    @Test
    fun intercept_skipsAuthHeader_forRefreshEndpoint() {
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/auth/refresh")
        every { chain.request() } returns original
        every { chain.proceed(original) } returns responseFor(original, 200)

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        assertEquals(200, result.code)
        assertNull(result.request.header("Authorization"))
        verify(exactly = 0) { tokenStore.isAccessTokenExpiringSoon() }
        verify(exactly = 0) { sessionManager.isLoggedIn() }
        coVerify(exactly = 0) { sessionManager.refresh() }
    }

    // ---- Synthesised 401 short-circuit --------------------------------------

    @Test
    fun intercept_whenLoggedOut_returnsSynthesised401_withoutHittingNetwork() {
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager(loggedIn = false)
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")
        every { chain.request() } returns original

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        assertEquals(401, result.code)
        verify(exactly = 0) { chain.proceed(any()) }
        verify(exactly = 0) { tokenStore.isAccessTokenExpiringSoon() }
        verify(exactly = 0) { tokenStore.getAccessToken() }
        coVerify(exactly = 0) { sessionManager.refresh() }
    }

    // ---- Bearer injection ---------------------------------------------------

    @Test
    fun intercept_injectsBearerHeader_whenTokenPresent_andNotExpiring() {
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")
        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns false
        every { tokenStore.getAccessToken() } returns "access-123"

        var forwardedAuth: String? = null
        every { chain.proceed(any()) } answers {
            val req = firstArg<Request>()
            forwardedAuth = req.header("Authorization")
            responseFor(req, 200)
        }

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        assertEquals(200, result.code)
        assertEquals("Bearer access-123", forwardedAuth)
        coVerify(exactly = 0) { sessionManager.refresh() }
    }

    @Test
    fun intercept_doesNotInjectBearer_whenTokenMissing() {
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")
        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns false
        every { tokenStore.getAccessToken() } returns null

        var forwardedAuth: String? = "sentinel"
        every { chain.proceed(any()) } answers {
            val req = firstArg<Request>()
            forwardedAuth = req.header("Authorization")
            assertSame(
                "no-token path must forward the original Request unchanged",
                original,
                req,
            )
            responseFor(req, 200)
        }

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        assertEquals(200, result.code)
        assertNull(forwardedAuth)
    }

    // ---- Expiring-soon pre-refresh ------------------------------------------

    @Test
    fun intercept_prefetchesRefresh_whenAccessTokenExpiringSoon() {
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")

        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns true
        every { tokenStore.getAccessToken() } returns "access-new"
        coEvery { sessionManager.refresh() } returns SessionManager.RefreshResult.Success

        var forwardedAuth: String? = null
        every { chain.proceed(any()) } answers {
            val req = firstArg<Request>()
            forwardedAuth = req.header("Authorization")
            responseFor(req, 200)
        }

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        assertEquals(200, result.code)
        assertEquals("Bearer access-new", forwardedAuth)
        coVerify(exactly = 1) { sessionManager.refresh() }
    }

    @Test
    fun intercept_whenPrefetchRefreshReturnsTerminal_returnsSynthesised401_withoutRequest() {
        // Terminal refresh means the session was killed inside SessionManager
        // (refresh token rejected). The interceptor must not send the
        // main request with a known-dead token.
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")

        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns true
        coEvery { sessionManager.refresh() } returns SessionManager.RefreshResult.Terminal

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        assertEquals(401, result.code)
        verify(exactly = 0) { chain.proceed(any()) }
        coVerify(exactly = 1) { sessionManager.refresh() }
    }

    @Test
    fun intercept_whenPrefetchRefreshIsTransient_proceedsWithCurrentToken() {
        // Transient = network flake. Keep the token, send the request;
        // if the server rejects it, the 401-retry branch kicks in below.
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")

        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns true
        every { tokenStore.getAccessToken() } returns "still-here"
        coEvery { sessionManager.refresh() } returns SessionManager.RefreshResult.Transient

        var forwardedAuth: String? = null
        every { chain.proceed(any()) } answers {
            val req = firstArg<Request>()
            forwardedAuth = req.header("Authorization")
            responseFor(req, 200)
        }

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        assertEquals(200, result.code)
        assertEquals("Bearer still-here", forwardedAuth)
    }

    // ---- 401 retry path -----------------------------------------------------

    @Test
    fun intercept_on401_refreshesOnce_andReplaysWithNewToken() {
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")

        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns false
        every { tokenStore.getAccessToken() } returnsMany listOf("old", "new")
        coEvery { sessionManager.refresh() } returns SessionManager.RefreshResult.Success

        val forwardedAuths = mutableListOf<String?>()
        var call = 0
        every { chain.proceed(any()) } answers {
            val req = firstArg<Request>()
            forwardedAuths += req.header("Authorization")
            call++
            if (call == 1) responseFor(req, 401) else responseFor(req, 200)
        }

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        assertEquals(200, result.code)
        assertEquals(2, forwardedAuths.size)
        assertEquals("Bearer old", forwardedAuths[0])
        assertEquals("Bearer new", forwardedAuths[1])
        coVerify(exactly = 1) { sessionManager.refresh() }
    }

    @Test
    fun intercept_on401_whenRefreshIsTerminal_returnsSynthesised401() {
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")

        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns false
        every { tokenStore.getAccessToken() } returns "old"
        coEvery { sessionManager.refresh() } returns SessionManager.RefreshResult.Terminal

        var proceedCalls = 0
        every { chain.proceed(any()) } answers {
            proceedCalls++
            responseFor(firstArg(), 401)
        }

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        assertEquals(401, result.code)
        // One proceed for the main request, one refresh via SessionManager
        // (single-flight), and NO silent retry with a stale token.
        assertEquals(1, proceedCalls)
        coVerify(exactly = 1) { sessionManager.refresh() }
    }

    @Test
    fun intercept_on401_whenInitialTokenWasNull_doesNotAttemptRefresh() {
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")

        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns false
        every { tokenStore.getAccessToken() } returns null

        var proceedCalls = 0
        every { chain.proceed(any()) } answers {
            proceedCalls++
            responseFor(firstArg(), 401)
        }

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        assertEquals(401, result.code)
        assertEquals(1, proceedCalls)
        coVerify(exactly = 0) { sessionManager.refresh() }
    }

    // ---- No-double-refresh guard --------------------------------------------

    @Test
    fun intercept_whenPrefetchRefreshSucceeds_doesNotRefreshAgainOn401() {
        // Regression guard: after a successful pre-emptive refresh, a
        // server-side 401 on the main request must NOT trigger a second
        // refresh within the same intercept() call. This prevents the
        // rapid double-refresh pattern that produced the original crash
        // logs (two POST /api/auth/refresh 401s ~23ms apart).
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")

        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns true
        every { tokenStore.getAccessToken() } returns "fresh-from-prefetch"
        coEvery { sessionManager.refresh() } returns SessionManager.RefreshResult.Success

        // Simulate the server still returning 401 on the main request
        // (e.g. its authorization DB is out of sync). The interceptor
        // must NOT re-refresh — just return the 401.
        every { chain.proceed(any()) } answers { responseFor(firstArg(), 401) }

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        assertEquals(401, result.code)
        coVerify(exactly = 1) { sessionManager.refresh() }
    }

    // ---- Synthesised response shape ----------------------------------------

    @Test
    fun synthesisedUnauthorized_carriesJsonBody_withDetailField() {
        // Ensures the upstream RestApi.execute() consumer can parse the
        // synthesised response just like a real server-rejected 401 and
        // surface a meaningful error via ApiException.
        val tokenStore = mockk<TokenStore>()
        val sessionManager = newSessionManager(loggedIn = false)
        val chain = mockk<Interceptor.Chain>()
        every { chain.request() } returns request("https://example.com/api/sessions")

        val result = AuthInterceptor(tokenStore, sessionManager).intercept(chain)

        val body = result.body?.string()
        assertNotNull(body)
        assertTrue(body!!.contains("Session expired"))
        assertTrue(body.startsWith("{") && body.endsWith("}"))
    }
}
