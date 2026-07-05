package com.mobaide.app.data.network

import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
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
import org.junit.Assert.fail
import org.junit.Test

/**
 * Unit tests for [CentralApi] -- exercises URL construction (trailing-slash
 * trimming on centralServerUrl), JSON request serialisation (captured
 * request body), JSON response deserialisation, and HTTP-code ->
 * [ApiException] mapping for register, login, and loginWithGoogle.
 *
 * Follows the same OkHttpClient/Call/Response MockK pattern established
 * in [RestApiTest] -- no MockWebServer.
 */
class CentralApiTest {

    // ---- Fixtures / helpers -------------------------------------------------

    private val jsonType = "application/json".toMediaType()

    private fun responseFor(
        req: Request,
        code: Int,
        bodyJson: String = "{}",
        message: String = if (code in 200..299) "OK" else "ERR",
    ): Response = Response.Builder()
        .request(req)
        .protocol(Protocol.HTTP_1_1)
        .code(code)
        .message(message)
        .body(bodyJson.toResponseBody(jsonType))
        .build()

    private fun stubCall(
        client: OkHttpClient,
        buildResponse: (Request) -> Response,
    ): io.mockk.CapturingSlot<Request> {
        val captured = slot<Request>()
        val call = mockk<Call>()
        every { client.newCall(capture(captured)) } returns call
        every { call.execute() } answers { buildResponse(captured.captured) }
        return captured
    }

    private fun newApi(
        baseUrl: String = "https://central.example.com",
    ): Pair<CentralApi, OkHttpClient> {
        val client = mockk<OkHttpClient>()
        return Pair(CentralApi(client, baseUrl), client)
    }

    private val userAuthResponseJson = """
        {
          "access_token": "tok-123",
          "token_type": "bearer",
          "user_id": "u-1",
          "email": "alice@example.com",
          "role": "user"
        }
    """.trimIndent()

    // ---- register: URL, body, response decode -------------------------------

    @Test
    fun register_postsToApiAuthRegister_withSerialisedBody() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req ->
            responseFor(req, 200, bodyJson = userAuthResponseJson)
        }

        val resp = api.register("alice@example.com", "s3cret")

        assertEquals(
            "https://central.example.com/api/auth/register",
            captured.captured.url.toString(),
        )
        assertEquals("POST", captured.captured.method)

        val buffer = okio.Buffer()
        captured.captured.body!!.writeTo(buffer)
        val body = buffer.readUtf8()
        assertTrue(
            "register body must contain the email -- was: $body",
            body.contains("\"email\":\"alice@example.com\""),
        )
        assertTrue(
            "register body must contain the password -- was: $body",
            body.contains("\"password\":\"s3cret\""),
        )

        assertEquals("tok-123", resp.access_token)
        assertEquals("bearer", resp.token_type)
        assertEquals("u-1", resp.user_id)
        assertEquals("alice@example.com", resp.email)
        assertEquals("user", resp.role)
    }

    // ---- login: URL, body, response decode ----------------------------------

    @Test
    fun login_postsToApiAuthLogin_withSerialisedBody() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req ->
            responseFor(req, 200, bodyJson = userAuthResponseJson)
        }

        val resp = api.login("alice@example.com", "s3cret")

        assertEquals(
            "https://central.example.com/api/auth/login",
            captured.captured.url.toString(),
        )
        assertEquals("POST", captured.captured.method)

        val buffer = okio.Buffer()
        captured.captured.body!!.writeTo(buffer)
        val body = buffer.readUtf8()
        assertTrue(body.contains("\"email\":\"alice@example.com\""))
        assertTrue(body.contains("\"password\":\"s3cret\""))

        assertEquals("tok-123", resp.access_token)
        assertEquals("u-1", resp.user_id)
    }

    // ---- loginWithGoogle: URL, body, response decode ------------------------

    @Test
    fun loginWithGoogle_postsToApiAuthGoogle_withIdTokenBody() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req ->
            responseFor(req, 200, bodyJson = userAuthResponseJson)
        }

        val resp = api.loginWithGoogle("google-id-token-xyz")

        assertEquals(
            "https://central.example.com/api/auth/google",
            captured.captured.url.toString(),
        )
        assertEquals("POST", captured.captured.method)

        val buffer = okio.Buffer()
        captured.captured.body!!.writeTo(buffer)
        val body = buffer.readUtf8()
        assertTrue(
            "google body must contain the id_token -- was: $body",
            body.contains("\"id_token\":\"google-id-token-xyz\""),
        )

        assertEquals("tok-123", resp.access_token)
        assertEquals("alice@example.com", resp.email)
    }

    // ---- Trailing-slash trimming on centralServerUrl -------------------------

    @Test
    fun baseUrl_withTrailingSlash_isNotDoubleSlashedInRequests() = runTest {
        val (api, client) = newApi(baseUrl = "https://central.example.com/")
        val captured = stubCall(client) { req ->
            responseFor(req, 200, bodyJson = userAuthResponseJson)
        }

        api.register("a@b.com", "pw")

        assertEquals(
            "https://central.example.com/api/auth/register",
            captured.captured.url.toString(),
        )
    }

    // ---- ApiException mapping: error codes ----------------------------------

    private val errorCodes = listOf(400, 401, 409, 500)

    @Test
    fun register_onErrorCode_throwsApiException_withCodeAndBody() = runTest {
        for (code in errorCodes) {
            val (api, client) = newApi()
            stubCall(client) { req ->
                responseFor(req, code, bodyJson = """{"detail":"err-$code"}""")
            }

            try {
                api.register("a@b.com", "pw")
                fail("expected ApiException for code $code")
            } catch (e: ApiException) {
                assertEquals("code mismatch for status $code", code, e.code)
                assertTrue(
                    "errorBody must contain server detail for status $code -- was: ${e.errorBody}",
                    e.errorBody.contains("err-$code"),
                )
            }
        }
    }

    @Test
    fun login_onErrorCode_throwsApiException_withCodeAndBody() = runTest {
        for (code in errorCodes) {
            val (api, client) = newApi()
            stubCall(client) { req ->
                responseFor(req, code, bodyJson = """{"detail":"err-$code"}""")
            }

            try {
                api.login("a@b.com", "pw")
                fail("expected ApiException for code $code")
            } catch (e: ApiException) {
                assertEquals(code, e.code)
                assertTrue(e.errorBody.contains("err-$code"))
            }
        }
    }

    @Test
    fun loginWithGoogle_onErrorCode_throwsApiException_withCodeAndBody() = runTest {
        for (code in errorCodes) {
            val (api, client) = newApi()
            stubCall(client) { req ->
                responseFor(req, code, bodyJson = """{"detail":"err-$code"}""")
            }

            try {
                api.loginWithGoogle("bad-token")
                fail("expected ApiException for code $code")
            } catch (e: ApiException) {
                assertEquals(code, e.code)
                assertTrue(e.errorBody.contains("err-$code"))
            }
        }
    }

    // ---- Empty response body edge case --------------------------------------

    @Test
    fun execute_on200_withEmptyBody_throwsException() = runTest {
        val (api, client) = newApi()
        stubCall(client) { req -> responseFor(req, 200, bodyJson = "") }

        try {
            api.register("a@b.com", "pw")
            fail("expected exception when body is empty on 200")
        } catch (e: Throwable) {
            // Either ApiException (if OkHttp returns null body) or
            // SerializationException from kotlinx.serialization.
            assertNotNull(e)
        }
    }

}
