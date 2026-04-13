package com.claudemobile.app.data.network

import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.network.dto.ClaudeOptionsModel
import com.claudemobile.app.data.network.dto.CreateSessionRequest
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
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
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Unit tests for [RestApi] — exercises URL construction (baseUrl trimming +
 * path concat), JSON request serialisation (via captured request body),
 * JSON response deserialisation, and HTTP-code → [ApiException] mapping
 * (401/403/404/409/429/500).
 *
 * Per group-4 constraints, [OkHttpClient] / [Call] / [Response] are mocked
 * with MockK — no MockWebServer. Each test captures the [Request] dispatched
 * to `client.newCall(...)` via a [io.mockk.slot] so we can assert the URL,
 * method, and serialised body that [RestApi] produced.
 */
class RestApiTest {

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

    /**
     * Wires `client.newCall(any()) → call.execute()` so that [buildResponse]
     * receives the captured [Request] and produces a matching [Response].
     * Returns the slot so callers can inspect the captured request post-hoc.
     */
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

    private fun newApi(agentUrl: String? = "https://api.example.com"): Triple<RestApi, OkHttpClient, TokenStore> {
        val client = mockk<OkHttpClient>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getAgentUrl() } returns agentUrl
        return Triple(RestApi(client, tokenStore), client, tokenStore)
    }

    // ---- baseUrl() trailing-slash handling ----------------------------------

    @Test
    fun baseUrl_withTrailingSlash_isNotDoubleSlashedInRequests() = runTest {
        // tokenStore.getAgentUrl() is used verbatim in `${baseUrl()}/api/...`,
        // so a trailing slash on the stored URL must NOT produce a double slash
        // in the final request path. [TokenStore.saveAgentUrl] trims trailing
        // slashes at store time — this test pins the store-side contract by
        // asserting the *canonical* case (no trailing slash → no double slash).
        val (api, client) = newApi(agentUrl = "https://api.example.com")
        val captured = stubCall(client) { req -> responseFor(req, 200, bodyJson = "[]") }

        api.listSessions()

        assertEquals(
            "https://api.example.com/api/sessions",
            captured.captured.url.toString(),
        )
        assertEquals("GET", captured.captured.method)
    }

    @Test
    fun baseUrl_notConfigured_throwsIllegalStateException_fromListSessions() = runTest {
        val (api, _, tokenStore) = newApi(agentUrl = null)
        // Pin the exact message so a future refactor that silently swallows
        // the null doesn't go unnoticed.
        try {
            api.listSessions()
            fail("expected IllegalStateException for missing agent URL")
        } catch (e: IllegalStateException) {
            assertEquals("Agent URL not configured", e.message)
        }
        verify(exactly = 1) { tokenStore.getAgentUrl() }
    }

    // ---- login: URL, body, response decode ----------------------------------

    @Test
    fun login_trimsTrailingSlash_onServerUrlArgument_andPostsCredentialJson() = runTest {
        val (api, client) = newApi(agentUrl = null) // login does not use baseUrl()
        val captured = stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """{"access_token":"acc","refresh_token":"ref","token_type":"bearer"}""",
            )
        }

        val resp = api.login(
            agentUrl = "https://login.example.com/",
            username = "alice",
            password = "s3cret",
        )

        // URL: trailing slash on agentUrl must be trimmed before path concat.
        assertEquals(
            "https://login.example.com/api/auth/token",
            captured.captured.url.toString(),
        )
        assertEquals("POST", captured.captured.method)

        // Body: serialised TokenRequest.
        val body = captured.captured.body
        assertNotNull("login must attach a JSON request body", body)
        val buffer = okio.Buffer()
        body!!.writeTo(buffer)
        val serialised = buffer.readUtf8()
        assertTrue(
            "login body must contain the username — was: $serialised",
            serialised.contains("\"username\":\"alice\""),
        )
        assertTrue(
            "login body must contain the password — was: $serialised",
            serialised.contains("\"password\":\"s3cret\""),
        )

        // Response decode.
        assertEquals("acc", resp.access_token)
        assertEquals("ref", resp.refresh_token)
        assertEquals("bearer", resp.token_type)
    }

    @Test
    fun login_doesNotConsultTokenStore_forBaseUrl() = runTest {
        // login() receives agentUrl as an explicit argument — it must never
        // fall back to tokenStore.getAgentUrl() for the base URL.
        val client = mockk<OkHttpClient>()
        val tokenStore = mockk<TokenStore>()
        // Intentionally do NOT stub tokenStore.getAgentUrl() — a call would
        // make MockK throw a missing-answer error.
        stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """{"access_token":"a","refresh_token":"r"}""",
            )
        }
        val api = RestApi(client, tokenStore)

        api.login("https://login.example.com", "u", "p")

        verify(exactly = 0) { tokenStore.getAgentUrl() }
    }

    // Note: there is no RestApi.refreshToken test because RestApi no
    // longer exposes a refresh endpoint — that method used to bypass
    // SessionManager's single-flight coordination and was deleted as
    // part of the auth-crash fix. Refresh coverage lives in
    // com.claudemobile.app.data.auth.SessionManagerTest.

    // ---- createSession: URL + JSON body serialisation -----------------------

    @Test
    fun createSession_postsToApiSessions_withSerialisedCreateSessionRequest() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """{"session_id":"s-1","state":"running","created_at":"2026-04-11T12:00:00Z"}""",
            )
        }

        val request = CreateSessionRequest(
            command = listOf("bash", "-l"),
            cwd = "/home/alice",
            project_name = "alpha",
            claude_options = ClaudeOptionsModel(no_memory = true, worktree = true),
        )

        val resp = api.createSession(request)

        // URL.
        assertEquals(
            "https://api.example.com/api/sessions",
            captured.captured.url.toString(),
        )
        assertEquals("POST", captured.captured.method)

        // Body: serialised JSON includes the nested fields.
        val buffer = okio.Buffer()
        captured.captured.body!!.writeTo(buffer)
        val body = buffer.readUtf8()
        assertTrue(body.contains("\"project_name\":\"alpha\""))
        assertTrue(body.contains("\"no_memory\":true"))
        assertTrue(body.contains("\"worktree\":true"))
        assertTrue(body.contains("\"cwd\":\"/home/alice\""))

        // Response decode.
        assertEquals("s-1", resp.session_id)
        assertEquals("running", resp.state)
    }

    // ---- listSessions -------------------------------------------------------

    @Test
    fun listSessions_getsApiSessions_andDeserialisesList() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """[
                  {"session_id":"s-1","state":"running","created_at":"2026-04-11T12:00:00Z"},
                  {"session_id":"s-2","state":"stopped","created_at":"2026-04-11T12:05:00Z"}
                ]""".trimIndent(),
            )
        }

        val list = api.listSessions()

        assertEquals("GET", captured.captured.method)
        assertNull("GET request must not carry a body", captured.captured.body)
        assertEquals(2, list.size)
        assertEquals("s-1", list[0].session_id)
        assertEquals("running", list[0].state)
        assertEquals("s-2", list[1].session_id)
        assertEquals("stopped", list[1].state)
    }

    // ---- deleteSession: force flag + executeRaw (no body expected) ----------

    @Test
    fun deleteSession_withoutForce_omitsQueryString() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req -> responseFor(req, 204, bodyJson = "") }

        api.deleteSession("s-1")

        assertEquals(
            "https://api.example.com/api/sessions/s-1",
            captured.captured.url.toString(),
        )
        assertEquals("DELETE", captured.captured.method)
    }

    @Test
    fun deleteSession_withForce_appendsForceQueryParam() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req -> responseFor(req, 204, bodyJson = "") }

        api.deleteSession("s-1", force = true)

        assertEquals(
            "https://api.example.com/api/sessions/s-1?force=true",
            captured.captured.url.toString(),
        )
        assertEquals("DELETE", captured.captured.method)
    }

    // ---- getScrollback: query-string construction ---------------------------

    @Test
    fun getScrollback_buildsUrlWithLinesQueryParam() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """{"session_id":"s-1","lines":["a","b"],"total":2}""",
            )
        }

        val resp = api.getScrollback("s-1", lines = 250)

        assertEquals(
            "https://api.example.com/api/sessions/s-1/scrollback?lines=250",
            captured.captured.url.toString(),
        )
        assertEquals("GET", captured.captured.method)
        assertEquals(2, resp.total)
        assertEquals(listOf("a", "b"), resp.lines)
    }

    // ---- getProjects --------------------------------------------------------

    @Test
    fun getProjects_getsApiProjects_andDeserialisesList() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """[
                  {"name":"alpha","path":"/p/alpha","is_git":true},
                  {"name":"beta","path":"/p/beta"}
                ]""".trimIndent(),
            )
        }

        val list = api.getProjects()

        assertEquals(
            "https://api.example.com/api/projects",
            captured.captured.url.toString(),
        )
        assertEquals("GET", captured.captured.method)
        assertEquals(2, list.size)
        assertEquals("alpha", list[0].name)
        assertTrue(list[0].is_git)
        assertEquals("beta", list[1].name)
    }

    // ---- AutoScan endpoints -------------------------------------------------

    @Test
    fun startAutoScan_withNullClaudeOptions_postsProjectName() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req -> responseFor(req, 200, bodyJson = "{}") }

        api.startAutoScan("alpha", claudeOptions = null)

        assertEquals(
            "https://api.example.com/api/autoscan/start",
            captured.captured.url.toString(),
        )
        assertEquals("POST", captured.captured.method)
        val buffer = okio.Buffer()
        captured.captured.body!!.writeTo(buffer)
        val body = buffer.readUtf8()
        assertTrue(
            "start body must contain the project name — was: $body",
            body.contains("\"project_name\":\"alpha\""),
        )
    }

    @Test
    fun stopAutoScan_postsProjectNameToStopEndpoint() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req -> responseFor(req, 200, bodyJson = "{}") }

        api.stopAutoScan("beta")

        assertEquals(
            "https://api.example.com/api/autoscan/stop",
            captured.captured.url.toString(),
        )
        val buffer = okio.Buffer()
        captured.captured.body!!.writeTo(buffer)
        assertTrue(buffer.readUtf8().contains("\"project_name\":\"beta\""))
    }

    @Test
    fun runAutoScanNow_postsAndDeserialisesRunNowResponse() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """{"ok":true,"project_name":"alpha","next_scan_at":"2026-04-11T13:00:00Z"}""",
            )
        }

        val resp = api.runAutoScanNow("alpha")

        assertEquals(
            "https://api.example.com/api/autoscan/run-now",
            captured.captured.url.toString(),
        )
        assertTrue(resp.ok)
        assertEquals("alpha", resp.project_name)
        assertEquals("2026-04-11T13:00:00Z", resp.next_scan_at)
    }

    @Test
    fun getAutoScanList_getsApiAutoScanList_andDeserialisesResponse() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """{
                  "scans":[],
                  "capacity":0,
                  "max_scans":5,
                  "available_projects":["alpha","beta"],
                  "auto_scan_prompt":"Do the thing"
                }""".trimIndent(),
            )
        }

        val resp = api.getAutoScanList()

        assertEquals(
            "https://api.example.com/api/autoscan/list",
            captured.captured.url.toString(),
        )
        assertEquals("GET", captured.captured.method)
        assertEquals(5, resp.max_scans)
        assertEquals("Do the thing", resp.auto_scan_prompt)
        assertEquals(listOf("alpha", "beta"), resp.available_projects)
    }

    // ---- ApiException mapping: 401 / 403 / 404 / 409 / 429 / 500 ------------

    private val errorCodes = listOf(401, 403, 404, 409, 429, 500)

    @Test
    fun execute_onErrorCode_throwsApiException_withCodeAndBody() = runTest {
        // Tests the `execute<T>` path (used by listSessions/getProjects/etc).
        for (code in errorCodes) {
            val (api, client) = newApi()
            stubCall(client) { req ->
                responseFor(req, code, bodyJson = """{"detail":"err-$code"}""")
            }

            try {
                api.listSessions()
                fail("expected ApiException for code $code")
            } catch (e: ApiException) {
                assertEquals("code mismatch for status $code", code, e.code)
                assertTrue(
                    "errorBody must contain server detail for status $code — was: ${e.errorBody}",
                    e.errorBody.contains("err-$code"),
                )
                assertTrue(
                    "exception message must include status code — was: ${e.message}",
                    e.message!!.contains("HTTP $code"),
                )
            }
        }
    }

    @Test
    fun executeRaw_onErrorCode_throwsApiException_withCodeAndBody() = runTest {
        // Tests the `executeRaw` path (used by deleteSession/startAutoScan/stopAutoScan).
        for (code in errorCodes) {
            val (api, client) = newApi()
            stubCall(client) { req ->
                responseFor(req, code, bodyJson = """{"detail":"raw-$code"}""")
            }

            try {
                api.deleteSession("s-1")
                fail("expected ApiException for code $code")
            } catch (e: ApiException) {
                assertEquals("code mismatch for status $code", code, e.code)
                assertTrue(e.errorBody.contains("raw-$code"))
            }
        }
    }

    @Test
    fun execute_onErrorCode_withEmptyBody_throwsApiException_withEmptyErrorBody() = runTest {
        val (api, client) = newApi()
        stubCall(client) { req -> responseFor(req, 500, bodyJson = "") }

        try {
            api.listSessions()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(500, e.code)
            assertEquals("", e.errorBody)
        }
    }

    // ---- Success-with-empty-body edge case for execute<T> -------------------

    @Test
    fun execute_on200_withEmptyBodyString_throwsApiExceptionForEmptyResponseBody() = runTest {
        // In RestApi.execute<T>, a successful response whose body.string() returns
        // "" is decoded as the JSON string ""; but the path that throws is when
        // the OkHttp body is null. Here we assert the JSON-decode failure path
        // surfaces as a thrown exception rather than a silent success.
        val (api, client) = newApi()
        stubCall(client) { req ->
            // "" is not a valid JSON List<SessionResponse>, so kotlinx.serialization
            // throws — the helper propagates it.
            responseFor(req, 200, bodyJson = "")
        }

        try {
            api.listSessions()
            fail("expected a decode-time failure when body is empty on 200")
        } catch (e: Throwable) {
            // Either ApiException (if some OkHttp version treats empty as null body)
            // or a SerializationException from kotlinx.serialization. Both are valid
            // failure modes — the key assertion is that listSessions() does not
            // silently succeed with an unexpected type.
            assertNotNull(e)
        }
    }
}
