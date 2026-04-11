package com.claudemobile.app.data.auth

import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [AuthInterceptor] — exercise the request-path decisions
 * (skip list, expiring-soon pre-refresh, Bearer injection) and the 401 →
 * refresh → replay retry path. [TokenStore] and [Interceptor.Chain] are
 * mocked with MockK; [Request] / [Response] are built with the real OkHttp
 * builders to keep the header/URL/code semantics realistic.
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

    // ---- Skip-list paths ----------------------------------------------------

    @Test
    fun intercept_skipsAuthHeader_forTokenEndpoint() {
        val tokenStore = mockk<TokenStore>()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/auth/token")
        every { chain.request() } returns original
        every { chain.proceed(any()) } answers {
            // Must forward the *original* request unchanged — no Authorization.
            val forwarded = firstArg<Request>()
            assertSame(
                "skip-path must not rewrite the request",
                original,
                forwarded,
            )
            assertNull(
                "skip-path must not attach a Bearer header",
                forwarded.header("Authorization"),
            )
            responseFor(forwarded, 200)
        }

        val result = AuthInterceptor(tokenStore).intercept(chain)

        assertEquals(200, result.code)
        // No token-store interaction at all on the skip path.
        verify(exactly = 0) { tokenStore.isAccessTokenExpiringSoon() }
        verify(exactly = 0) { tokenStore.getAccessToken() }
        verify(exactly = 0) { tokenStore.clear() }
    }

    @Test
    fun intercept_skipsAuthHeader_forRefreshEndpoint() {
        val tokenStore = mockk<TokenStore>()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/auth/refresh")
        every { chain.request() } returns original
        every { chain.proceed(original) } returns responseFor(original, 200)

        val result = AuthInterceptor(tokenStore).intercept(chain)

        assertEquals(200, result.code)
        assertNull(result.request.header("Authorization"))
        verify(exactly = 0) { tokenStore.isAccessTokenExpiringSoon() }
        verify(exactly = 0) { tokenStore.getAccessToken() }
    }

    // ---- Bearer injection ---------------------------------------------------

    @Test
    fun intercept_injectsBearerHeader_whenTokenPresent_andNotExpiring() {
        val tokenStore = mockk<TokenStore>()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")
        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns false
        every { tokenStore.getAccessToken() } returns "access-123"

        // Capture the forwarded request so we can assert the injected header.
        var forwardedAuth: String? = null
        every { chain.proceed(any()) } answers {
            val req = firstArg<Request>()
            forwardedAuth = req.header("Authorization")
            responseFor(req, 200)
        }

        val result = AuthInterceptor(tokenStore).intercept(chain)

        assertEquals(200, result.code)
        assertEquals("Bearer access-123", forwardedAuth)
        verify(exactly = 0) { tokenStore.clear() }
    }

    @Test
    fun intercept_doesNotInjectBearer_whenTokenMissing() {
        val tokenStore = mockk<TokenStore>()
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

        val result = AuthInterceptor(tokenStore).intercept(chain)

        assertEquals(200, result.code)
        assertNull(forwardedAuth)
        verify(exactly = 0) { tokenStore.clear() }
    }

    // ---- Expiring-soon pre-refresh ------------------------------------------

    @Test
    fun intercept_prefetchesRefresh_whenAccessTokenExpiringSoon() {
        val tokenStore = mockk<TokenStore>(relaxUnitFun = true)
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")

        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns true
        every { tokenStore.getRefreshToken() } returns "refresh-old"
        every { tokenStore.getServerUrl() } returns "https://example.com"
        // After the pre-refresh succeeds the interceptor re-reads getAccessToken()
        // to build the Bearer header for the actual request.
        every { tokenStore.getAccessToken() } returns "access-new"

        val forwardedUrls = mutableListOf<String>()
        val forwardedAuths = mutableListOf<String?>()
        every { chain.proceed(any()) } answers {
            val req = firstArg<Request>()
            forwardedUrls += req.url.toString()
            forwardedAuths += req.header("Authorization")
            if (req.url.encodedPath == "/api/auth/refresh") {
                responseFor(
                    req,
                    200,
                    bodyJson = """{"access_token":"access-new","refresh_token":"refresh-new"}""",
                )
            } else {
                responseFor(req, 200)
            }
        }

        val result = AuthInterceptor(tokenStore).intercept(chain)

        assertEquals(200, result.code)
        // First call = refresh, second = actual request with fresh Bearer.
        assertEquals(2, forwardedUrls.size)
        assertTrue(
            "first hop must be the refresh endpoint",
            forwardedUrls[0].endsWith("/api/auth/refresh"),
        )
        assertEquals("https://example.com/api/sessions", forwardedUrls[1])
        assertNull("refresh request must not carry a Bearer header", forwardedAuths[0])
        assertEquals("Bearer access-new", forwardedAuths[1])
        verify(exactly = 1) {
            tokenStore.saveTokens("access-new", "refresh-new")
        }
        verify(exactly = 0) { tokenStore.clear() }
    }

    // ---- 401 retry path -----------------------------------------------------

    @Test
    fun intercept_on401_refreshesOnce_andReplaysWithNewToken() {
        val tokenStore = mockk<TokenStore>(relaxUnitFun = true)
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")

        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns false
        // 1st read = initial Bearer ("old"); 2nd read = retry Bearer ("new").
        every { tokenStore.getAccessToken() } returnsMany listOf("old", "new")
        every { tokenStore.getRefreshToken() } returns "refresh-old"
        every { tokenStore.getServerUrl() } returns "https://example.com"

        val forwardedAuths = mutableListOf<String?>()
        val forwardedUrls = mutableListOf<String>()
        every { chain.proceed(any()) } answers {
            val req = firstArg<Request>()
            forwardedUrls += req.url.toString()
            forwardedAuths += req.header("Authorization")
            when {
                req.url.encodedPath == "/api/auth/refresh" -> responseFor(
                    req,
                    200,
                    bodyJson = """{"access_token":"new","refresh_token":"refresh-new"}""",
                )
                forwardedUrls.size == 1 -> responseFor(req, 401)
                else -> responseFor(req, 200)
            }
        }

        val result = AuthInterceptor(tokenStore).intercept(chain)

        assertEquals(200, result.code)
        // Three proceed hops: initial(401) → refresh(200) → retry(200).
        assertEquals(3, forwardedUrls.size)
        assertEquals("https://example.com/api/sessions", forwardedUrls[0])
        assertTrue(forwardedUrls[1].endsWith("/api/auth/refresh"))
        assertEquals("https://example.com/api/sessions", forwardedUrls[2])
        assertEquals("Bearer old", forwardedAuths[0])
        assertNull(forwardedAuths[1])
        assertEquals("Bearer new", forwardedAuths[2])
        verify(exactly = 1) { tokenStore.saveTokens("new", "refresh-new") }
        verify(exactly = 0) { tokenStore.clear() }
    }

    @Test
    fun intercept_on401_whenRefreshFails_clearsTokenStore_andReturns401() {
        val tokenStore = mockk<TokenStore>()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")

        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns false
        every { tokenStore.getAccessToken() } returns "old"
        every { tokenStore.getRefreshToken() } returns "refresh-old"
        every { tokenStore.getServerUrl() } returns "https://example.com"
        every { tokenStore.clear() } just Runs

        val forwardedUrls = mutableListOf<String>()
        every { chain.proceed(any()) } answers {
            val req = firstArg<Request>()
            forwardedUrls += req.url.toString()
            when {
                req.url.encodedPath == "/api/auth/refresh" -> responseFor(req, 500)
                else -> responseFor(req, 401)
            }
        }

        val result = AuthInterceptor(tokenStore).intercept(chain)

        // After a failed refresh the interceptor returns the original 401 response;
        // it must NOT make a third proceed call (no silent retry with a stale token).
        assertEquals(401, result.code)
        assertEquals(2, forwardedUrls.size)
        assertEquals("https://example.com/api/sessions", forwardedUrls[0])
        assertTrue(forwardedUrls[1].endsWith("/api/auth/refresh"))
        verify(exactly = 1) { tokenStore.clear() }
    }

    @Test
    fun intercept_on401_whenNoRefreshToken_doesNotRetry_andClearsStore() {
        // refreshTokenSync() short-circuits to false when getRefreshToken() is null,
        // so the outer branch still hits tokenStore.clear().
        val tokenStore = mockk<TokenStore>()
        val chain = mockk<Interceptor.Chain>()
        val original = request("https://example.com/api/sessions")

        every { chain.request() } returns original
        every { tokenStore.isAccessTokenExpiringSoon() } returns false
        every { tokenStore.getAccessToken() } returns "old"
        every { tokenStore.getRefreshToken() } returns null
        every { tokenStore.clear() } just Runs

        val forwardedUrls = mutableListOf<String>()
        every { chain.proceed(any()) } answers {
            forwardedUrls += firstArg<Request>().url.toString()
            responseFor(firstArg(), 401)
        }

        val result = AuthInterceptor(tokenStore).intercept(chain)

        assertEquals(401, result.code)
        assertEquals(1, forwardedUrls.size)
        verify(exactly = 1) { tokenStore.clear() }
        // getServerUrl should never be consulted when refresh token is absent.
        verify(exactly = 0) { tokenStore.getServerUrl() }
    }

    @Test
    fun intercept_on401_whenInitialTokenWasNull_doesNotAttemptRefresh() {
        // With no initial token the code path `response.code == 401 && token != null`
        // is false, so the raw 401 is returned without touching refreshTokenSync
        // or clear().
        val tokenStore = mockk<TokenStore>()
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

        val result = AuthInterceptor(tokenStore).intercept(chain)

        assertEquals(401, result.code)
        assertEquals(1, proceedCalls)
        verify(exactly = 0) { tokenStore.clear() }
        verify(exactly = 0) { tokenStore.getRefreshToken() }
    }
}
