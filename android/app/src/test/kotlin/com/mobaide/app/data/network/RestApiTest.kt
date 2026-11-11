package com.mobaide.app.data.network

import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.dto.ClaudeOptionsModel
import com.mobaide.app.data.network.dto.CreateSessionRequest
import com.mobaide.app.data.network.dto.CliModelDetail
import com.mobaide.app.data.network.dto.cliAvailable
import com.mobaide.app.data.network.dto.cliModelDetails
import com.mobaide.app.data.network.dto.cliModelsPending
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
import org.junit.Assert.assertFalse
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
        every { tokenStore.getEffectiveBaseUrl() } returns agentUrl
        return Triple(RestApi(client, tokenStore), client, tokenStore)
    }

    // ---- baseUrl() trailing-slash handling ----------------------------------

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
        verify(exactly = 1) { tokenStore.getEffectiveBaseUrl() }
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

    // Note: there is no RestApi.refreshToken test because RestApi no
    // longer exposes a refresh endpoint — that method used to bypass
    // SessionManager's single-flight coordination and was deleted as
    // part of the auth-crash fix. Refresh coverage lives in
    // com.mobaide.app.data.auth.SessionManagerTest.

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

    @Test
    fun createSession_claudeDebug_reachesTheWireOnlyWhenTheToggleIsOn() = runTest {
        // RestApi's Json omits fields left at their declared default, so OFF
        // is legitimately absent. The ON case is the one that would silently
        // vanish if the DTO default were ever flipped to true the way
        // skip_permissions has to be.
        val (api, client) = newApi()
        val captured = stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """{"session_id":"s-1","state":"running","created_at":"2026-08-11T00:00:00Z"}""",
            )
        }

        api.createSession(
            CreateSessionRequest(
                project_name = "alpha",
                claude_options = ClaudeOptionsModel(debug = false),
            ),
        )
        assertFalse(
            "an off toggle is indistinguishable from absent, which the server reads as off",
            bodyOf(captured.captured).contains("\"debug\""),
        )

        api.createSession(
            CreateSessionRequest(
                project_name = "alpha",
                claude_options = ClaudeOptionsModel(debug = true),
            ),
        )
        assertTrue(bodyOf(captured.captured).contains("\"debug\":true"))
    }

    @Test
    fun createSession_focusMode_sendsBothValuesExplicitly_andOmitsAnUnsetOne() = runTest {
        // Unlike debug, focus_mode defaults to null: false must reach the wire so
        // the agent pins viewMode "default" instead of leaving a sticky /focus on.
        val (api, client) = newApi()
        val captured = stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """{"session_id":"s-1","state":"running","created_at":"2026-10-19T00:00:00Z"}""",
            )
        }

        api.createSession(
            CreateSessionRequest(project_name = "alpha", claude_options = ClaudeOptionsModel(focus_mode = false)),
        )
        assertTrue(bodyOf(captured.captured).contains("\"focus_mode\":false"))

        api.createSession(
            CreateSessionRequest(project_name = "alpha", claude_options = ClaudeOptionsModel(focus_mode = true)),
        )
        assertTrue(bodyOf(captured.captured).contains("\"focus_mode\":true"))

        api.createSession(
            CreateSessionRequest(project_name = "alpha", claude_options = ClaudeOptionsModel()),
        )
        assertFalse(bodyOf(captured.captured).contains("\"focus_mode\""))
    }

    private fun bodyOf(request: Request): String {
        val buffer = okio.Buffer()
        request.body!!.writeTo(buffer)
        return buffer.readUtf8()
    }

    // ---- deleteSession: force flag + executeRaw (no body expected) ----------

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

    // ---- getAgentVersion: capabilities.cli availability parse ----------------

    @Test
    fun getAgentVersion_getsApiVersion_andParsesCliAvailability() = runTest {
        // Full wire → DTO → cliAvailable() path: the agent reports claude
        // installed and codex not, and the tolerant extension must read both
        // booleans out of the nested capabilities.cli.<id>.available shape.
        val (api, client) = newApi()
        val captured = stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """
                  {
                    "name":"mobaide_agent",
                    "server_version":"0.2.16",
                    "protocol_versions":[5],
                    "min_client_version":5,
                    "capabilities":{"cli":{"claude":{"available":true},"codex":{"available":false}}},
                    "latest_agent_version":null
                  }
                """.trimIndent(),
            )
        }

        val resp = api.getAgentVersion()

        assertEquals(
            "https://api.example.com/api/version",
            captured.captured.url.toString(),
        )
        assertEquals("GET", captured.captured.method)
        assertEquals(true, resp.cliAvailable("claude"))
        assertEquals(false, resp.cliAvailable("codex"))
    }

    @Test
    fun getAgentVersion_oldAgentEmptyCapabilities_cliAvailableReturnsNull() = runTest {
        // An older agent that predates the cli field returns capabilities: {}.
        // cliAvailable must return null (not false) so the caller stays
        // optimistic and never disables a mode whose CLI is actually present.
        val (api, client) = newApi()
        stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """
                  {
                    "name":"mobaide_agent",
                    "server_version":"0.2.0",
                    "protocol_versions":[5],
                    "min_client_version":5,
                    "capabilities":{},
                    "latest_agent_version":null
                  }
                """.trimIndent(),
            )
        }

        val resp = api.getAgentVersion()

        assertNull("absent cli key must read as null (unknown), not false", resp.cliAvailable("claude"))
        assertNull(resp.cliAvailable("codex"))
    }

    @Test
    fun getAgentVersion_malformedCliShape_returnsNull_withoutThrowing() = runTest {
        // capabilities.cli is the wrong shape (a string, and an id whose value
        // is a bare bool rather than an object). The loose JsonObject walk must
        // swallow the mismatch and return null per id — never throw, which would
        // surface a probe failure as a user-facing error.
        val (api, client) = newApi()
        stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """
                  {
                    "name":"mobaide_agent",
                    "server_version":"0.2.16",
                    "protocol_versions":[5],
                    "min_client_version":5,
                    "capabilities":{"cli":"not-an-object"},
                    "latest_agent_version":null
                  }
                """.trimIndent(),
            )
        }

        val resp = api.getAgentVersion()

        assertNull("malformed cli map must not throw", resp.cliAvailable("claude"))
    }

    @Test
    fun getAgentVersion_modelDetails_parsesPerModelEfforts() = runTest {
        // capabilities.cli.codex.model_details → cliModelDetails(): entries with
        // a usable id + non-empty efforts parse; default_effort is optional
        // (omitted on the wire when the catalog names none → null here).
        val (api, client) = newApi()
        stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """
                  {
                    "name":"mobaide_agent",
                    "server_version":"0.3.10",
                    "protocol_versions":[7],
                    "min_client_version":7,
                    "capabilities":{"cli":{"codex":{
                      "available":true,
                      "models":["gpt-5.6-sol","gpt-5.4-mini"],
                      "model_details":[
                        {"id":"gpt-5.6-sol","efforts":["low","high","ultra"],"default_effort":"low"},
                        {"id":"gpt-5.4-mini","efforts":["low"]}
                      ]
                    }}},
                    "latest_agent_version":null
                  }
                """.trimIndent(),
            )
        }

        val resp = api.getAgentVersion()

        assertEquals(
            listOf(
                CliModelDetail("gpt-5.6-sol", listOf("low", "high", "ultra"), "low"),
                CliModelDetail("gpt-5.4-mini", listOf("low"), null),
            ),
            resp.cliModelDetails("codex"),
        )
        assertNull("no details advertised for claude", resp.cliModelDetails("claude"))
    }

    @Test
    fun getAgentVersion_modelDetailsAbsentOrMalformed_returnsNull_withoutThrowing() = runTest {
        // Unusable entries (no id, empty/missing efforts, wrong shapes) are
        // dropped; when nothing survives — or the key is absent entirely — the
        // walk must yield null so callers fall back to the static effort list,
        // and it must never throw.
        val (api, client) = newApi()
        stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """
                  {
                    "name":"mobaide_agent",
                    "server_version":"0.3.10",
                    "protocol_versions":[7],
                    "min_client_version":7,
                    "capabilities":{"cli":{
                      "codex":{"model_details":[
                        "not-an-object",
                        {"efforts":["low"]},
                        {"id":"","efforts":["low"]},
                        {"id":"no-efforts"},
                        {"id":"empty-efforts","efforts":[]}
                      ]},
                      "antigravity":{"model_details":"not-an-array"},
                      "claude":{"available":true}
                    }},
                    "latest_agent_version":null
                  }
                """.trimIndent(),
            )
        }

        val resp = api.getAgentVersion()

        assertNull("all-unusable entries must collapse to null", resp.cliModelDetails("codex"))
        assertNull("non-array shape must not throw", resp.cliModelDetails("antigravity"))
        assertNull("absent key must map to null", resp.cliModelDetails("claude"))
    }

    @Test
    fun getAgentVersion_modelsPending_parsesMarker_tolerantly() = runTest {
        // capabilities.cli.<id>.models_pending → cliModelsPending(): true only
        // for a literal boolean true; absent keys (old agents / finished
        // warmup) and unreadable shapes read as false, never throw.
        val (api, client) = newApi()
        stubCall(client) { req ->
            responseFor(
                req,
                200,
                bodyJson = """
                  {
                    "name":"mobaide_agent",
                    "server_version":"0.3.10",
                    "protocol_versions":[7],
                    "min_client_version":7,
                    "capabilities":{"cli":{
                      "codex":{"available":true,"models_pending":true},
                      "antigravity":{"available":true,"models_pending":"yes"},
                      "claude":{"available":true}
                    }},
                    "latest_agent_version":null
                  }
                """.trimIndent(),
            )
        }

        val resp = api.getAgentVersion()

        assertEquals(true, resp.cliModelsPending("codex"))
        assertEquals(false, resp.cliModelsPending("antigravity"))
        assertEquals(false, resp.cliModelsPending("claude"))
        assertEquals(false, resp.cliModelsPending("missing"))
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
}
