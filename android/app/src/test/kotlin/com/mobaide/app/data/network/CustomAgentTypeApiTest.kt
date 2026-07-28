package com.mobaide.app.data.network

import com.mobaide.app.data.network.dto.UpdateCustomAgentTypeRequest
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
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Wire-contract tests for the [CentralApi] custom-agent-type endpoints
 * (proto-v7, `/api/custom-agent-types`): URL + HTTP method construction
 * (including the non-standard PATCH), the user-token Authorization header,
 * request-body shape — notably that the PATCH encoder drops null-defaulted
 * fields, since `{"name": null}` and `{}` are server-side 422s — and
 * [ApiException] mapping for the 409s the editor surfaces inline.
 *
 * Follows the OkHttpClient/Call/Response MockK pattern of [CentralApiTest].
 */
class CustomAgentTypeApiTest {

    private val jsonType = "application/json".toMediaType()

    private fun responseFor(
        req: Request,
        code: Int,
        bodyJson: String = "{}",
    ): Response = Response.Builder()
        .request(req)
        .protocol(Protocol.HTTP_1_1)
        .code(code)
        .message(if (code in 200..299) "OK" else "ERR")
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

    private fun newApi(): Pair<CentralApi, OkHttpClient> {
        val client = mockk<OkHttpClient>()
        return Pair(CentralApi(client, "https://central.example.com"), client)
    }

    private fun bodyOf(req: Request): String {
        val buffer = okio.Buffer()
        req.body!!.writeTo(buffer)
        return buffer.readUtf8()
    }

    private val typeJson = """
        {
          "id": "ab12cd34ef56",
          "name": "Aider",
          "command": "aider --model sonnet",
          "created_at": "2026-07-28T12:00:00Z",
          "updated_at": "2026-07-28T12:00:00Z"
        }
    """.trimIndent()

    @Test
    fun listCustomAgentTypes_getsCollectionUrl_withBearerToken_andDecodesList() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req -> responseFor(req, 200, "[$typeJson]") }

        val types = api.listCustomAgentTypes("tok-user")

        assertEquals(
            "https://central.example.com/api/custom-agent-types",
            captured.captured.url.toString(),
        )
        assertEquals("GET", captured.captured.method)
        assertEquals("Bearer tok-user", captured.captured.header("Authorization"))
        assertEquals(listOf("ab12cd34ef56"), types.map { it.id })
        assertEquals("aider --model sonnet", types.single().command)
    }

    @Test
    fun createCustomAgentType_postsNameAndCommand_andDecodesDto() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req -> responseFor(req, 200, typeJson) }

        val created = api.createCustomAgentType("tok-user", "Aider", "aider --model sonnet")

        assertEquals(
            "https://central.example.com/api/custom-agent-types",
            captured.captured.url.toString(),
        )
        assertEquals("POST", captured.captured.method)
        val body = bodyOf(captured.captured)
        assertTrue("create body must carry name -- was: $body", body.contains("\"name\":\"Aider\""))
        assertTrue(
            "create body must carry command -- was: $body",
            body.contains("\"command\":\"aider --model sonnet\""),
        )
        assertEquals("ab12cd34ef56", created.id)
    }

    @Test
    fun updateCustomAgentType_usesPatchMethod_onTypeIdUrl() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req -> responseFor(req, 200, typeJson) }

        api.updateCustomAgentType(
            "tok-user",
            "ab12cd34ef56",
            UpdateCustomAgentTypeRequest(name = "New", command = "new-cmd"),
        )

        assertEquals(
            "https://central.example.com/api/custom-agent-types/ab12cd34ef56",
            captured.captured.url.toString(),
        )
        assertEquals("PATCH", captured.captured.method)
        val body = bodyOf(captured.captured)
        assertTrue(body.contains("\"name\":\"New\""))
        assertTrue(body.contains("\"command\":\"new-cmd\""))
    }

    @Test
    fun updateCustomAgentType_dropsNullDefaultedFields_fromPatchBody() = runTest {
        // `{"command": null}` is a 422 server-side — the encoder must omit an
        // unset field entirely, not serialise it as null.
        val (api, client) = newApi()
        val captured = stubCall(client) { req -> responseFor(req, 200, typeJson) }

        api.updateCustomAgentType(
            "tok-user",
            "ab12cd34ef56",
            UpdateCustomAgentTypeRequest(name = "OnlyName"),
        )

        val body = bodyOf(captured.captured)
        assertTrue(body.contains("\"name\":\"OnlyName\""))
        assertFalse(
            "an unset PATCH field must be omitted, never null -- was: $body",
            body.contains("command"),
        )
    }

    @Test
    fun deleteCustomAgentType_deletesTypeIdUrl_withBearerToken() = runTest {
        val (api, client) = newApi()
        val captured = stubCall(client) { req -> responseFor(req, 204, "") }

        api.deleteCustomAgentType("tok-user", "ab12cd34ef56")

        assertEquals(
            "https://central.example.com/api/custom-agent-types/ab12cd34ef56",
            captured.captured.url.toString(),
        )
        assertEquals("DELETE", captured.captured.method)
        assertEquals("Bearer tok-user", captured.captured.header("Authorization"))
    }

    @Test
    fun createCustomAgentType_on409_throwsApiException_withServerDetailBody() = runTest {
        // The 409 detail (20-type cap / name conflict) is user-grade copy the
        // editor shows verbatim — it must survive into ApiException.errorBody.
        val (api, client) = newApi()
        stubCall(client) { req ->
            responseFor(req, 409, """{"detail":"Custom agent type limit reached (20)"}""")
        }

        try {
            api.createCustomAgentType("tok-user", "Aider", "aider")
            fail("expected ApiException for 409")
        } catch (e: ApiException) {
            assertEquals(409, e.code)
            assertTrue(e.errorBody.contains("Custom agent type limit reached (20)"))
        }
    }
}
