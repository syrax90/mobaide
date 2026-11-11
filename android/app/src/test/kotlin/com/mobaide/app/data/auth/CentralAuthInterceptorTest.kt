package com.mobaide.app.data.auth

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
import org.junit.Test

/**
 * Unit tests for [CentralAuthInterceptor] — exercises the sole
 * responsibility of this interceptor: detect a 401 from the central
 * server (dashboard-JWT-authenticated endpoints) and signal the session
 * as invalid via [SessionManager.invalidateUserSession]. Pre-auth
 * endpoints (register / login / Google) are excluded so that
 * wrong-password 401s do not clobber a logged-in user's session.
 *
 * [SessionManager] is mocked; [Request] / [Response] use the real OkHttp
 * builders to keep URL and code semantics realistic.
 */
class CentralAuthInterceptorTest {

    private fun request(url: String): Request =
        Request.Builder().url(url).get().build()

    private fun responseFor(req: Request, code: Int, bodyJson: String = "{}"): Response =
        Response.Builder()
            .request(req)
            .protocol(Protocol.HTTP_1_1)
            .code(code)
            .message(if (code in 200..299) "OK" else "ERR")
            .body(bodyJson.toResponseBody("application/json".toMediaType()))
            .build()

    @Test
    fun success_passesThrough_withoutInvalidating() {
        val sm = mockk<SessionManager>(relaxUnitFun = true)
        val chain = mockk<Interceptor.Chain>()
        val req = request("https://central.example.com/api/agents")
        every { chain.request() } returns req
        every { chain.proceed(req) } returns responseFor(req, 200)

        val result = CentralAuthInterceptor(sm).intercept(chain)

        assertEquals(200, result.code)
        verify(exactly = 0) { sm.invalidateUserSession(any()) }
    }

    @Test
    fun unauthorized_onAgentListing_invalidatesSession_withTokenRejected() {
        val sm = mockk<SessionManager>(relaxUnitFun = true)
        val chain = mockk<Interceptor.Chain>()
        val req = request("https://central.example.com/api/agents")
        every { chain.request() } returns req
        every { chain.proceed(req) } returns responseFor(req, 401, """{"detail":"Invalid token"}""")

        val result = CentralAuthInterceptor(sm).intercept(chain)

        assertEquals(401, result.code)
        verify(exactly = 1) {
            sm.invalidateUserSession(SessionManager.InvalidationReason.TokenRejected)
        }
    }

    @Test
    fun unauthorized_onLoginEndpoint_doesNotInvalidate() {
        // Wrong password legitimately produces a 401 on /api/auth/login.
        // The user isn't logged in yet, so there's no session to invalidate.
        val sm = mockk<SessionManager>(relaxUnitFun = true)
        val chain = mockk<Interceptor.Chain>()
        val req = request("https://central.example.com/api/auth/login")
        every { chain.request() } returns req
        every { chain.proceed(req) } returns responseFor(
            req, 401, """{"detail":"Invalid email or password"}""",
        )

        val result = CentralAuthInterceptor(sm).intercept(chain)

        assertEquals(401, result.code)
        verify(exactly = 0) { sm.invalidateUserSession(any()) }
    }

    @Test
    fun unauthorized_onRegisterEndpoint_doesNotInvalidate() {
        val sm = mockk<SessionManager>(relaxUnitFun = true)
        val chain = mockk<Interceptor.Chain>()
        val req = request("https://central.example.com/api/auth/register")
        every { chain.request() } returns req
        every { chain.proceed(req) } returns responseFor(req, 401)

        CentralAuthInterceptor(sm).intercept(chain)

        verify(exactly = 0) { sm.invalidateUserSession(any()) }
    }

    @Test
    fun unauthorized_onGoogleEndpoint_doesNotInvalidate() {
        val sm = mockk<SessionManager>(relaxUnitFun = true)
        val chain = mockk<Interceptor.Chain>()
        val req = request("https://central.example.com/api/auth/google")
        every { chain.request() } returns req
        every { chain.proceed(req) } returns responseFor(req, 401)

        CentralAuthInterceptor(sm).intercept(chain)

        verify(exactly = 0) { sm.invalidateUserSession(any()) }
    }

    @Test
    fun non401_errorCodes_passThrough_withoutInvalidating() {
        // Only 401 means "your token was rejected". 500, 404, 403 etc.
        // are orthogonal failures and should not kick the user out.
        val sm = mockk<SessionManager>(relaxUnitFun = true)
        val chain = mockk<Interceptor.Chain>()
        val req = request("https://central.example.com/api/agents")
        every { chain.request() } returns req
        every { chain.proceed(req) } returns responseFor(req, 500, """{"detail":"boom"}""")

        val result = CentralAuthInterceptor(sm).intercept(chain)

        assertEquals(500, result.code)
        verify(exactly = 0) { sm.invalidateUserSession(any()) }
    }

    @Test
    fun unauthorized_onLoginEndpoint_withQueryString_doesNotInvalidate() {
        // Query strings must not break the pre-auth path bypass. OkHttp's
        // encodedPath strips the query, but a regression test pins the
        // behaviour so a future refactor that switches to request.url.toString()
        // or similar does not silently re-introduce the session-clobbering
        // bug for wrong-password 401s.
        val sm = mockk<SessionManager>(relaxUnitFun = true)
        val chain = mockk<Interceptor.Chain>()
        val req = request("https://central.example.com/api/auth/login?returnTo=%2Fdashboard")
        every { chain.request() } returns req
        every { chain.proceed(req) } returns responseFor(req, 401)

        CentralAuthInterceptor(sm).intercept(chain)

        verify(exactly = 0) { sm.invalidateUserSession(any()) }
    }

    @Test
    fun unauthorized_onLookAlikePath_stillInvalidates() {
        // Regression guard against `contains`-style refactors: a
        // pre-auth-adjacent path like `/api/auth/login2` or
        // `/api/auth/login/foo` is NOT in the bypass list and must
        // invalidate the session on 401, same as any other
        // dashboard-JWT endpoint. The bypass uses strict endsWith; this
        // test proves a drifted predicate would fail loudly.
        val sm = mockk<SessionManager>(relaxUnitFun = true)
        val chain = mockk<Interceptor.Chain>()
        val req = request("https://central.example.com/api/auth/login2")
        every { chain.request() } returns req
        every { chain.proceed(req) } returns responseFor(req, 401)

        CentralAuthInterceptor(sm).intercept(chain)

        verify(exactly = 1) {
            sm.invalidateUserSession(SessionManager.InvalidationReason.TokenRejected)
        }
    }
}
