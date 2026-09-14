package com.mobaide.app.data.network

import com.mobaide.app.data.auth.TokenStore
import io.mockk.CapturingSlot
import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody
import okhttp3.ResponseBody.Companion.asResponseBody
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import okio.Source
import okio.Timeout
import okio.buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException

/**
 * URL construction and the cancellable transport of the session-scoped git
 * Changes routes. Same mocked [OkHttpClient] / [Call] arrangement as
 * `RestApiTest`, except that these routes `enqueue` rather than `execute`,
 * so the stub answers through the [Callback] it is handed.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class RestApiSessionGitTest {

    private val jsonType = "application/json".toMediaType()

    private class Fixture(val api: RestApi, val captured: CapturingSlot<Request>, val call: Call)

    private fun responseFor(req: Request, bodyJson: String, code: Int = 200): Response =
        responseFor(req, bodyJson.toResponseBody(jsonType), code)

    private fun responseFor(req: Request, body: ResponseBody, code: Int = 200): Response = Response.Builder()
        .request(req)
        .protocol(Protocol.HTTP_1_1)
        .code(code)
        .message(if (code == 200) "OK" else "Error")
        .body(body)
        .build()

    /** [answer] is what the mocked [Call] does with the [Callback] it is enqueued with. */
    private fun newFixture(answer: (Call, Request, Callback) -> Unit): Fixture {
        val client = mockk<OkHttpClient>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getEffectiveBaseUrl() } returns "https://api.example.com"
        val captured = slot<Request>()
        val call = mockk<Call>()
        every { client.newCall(capture(captured)) } returns call
        every { call.enqueue(any()) } answers { answer(call, captured.captured, firstArg<Callback>()) }
        return Fixture(RestApi(client, tokenStore), captured, call)
    }

    private fun newApi(bodyJson: String, code: Int = 200): Pair<RestApi, CapturingSlot<Request>> {
        val f = newFixture { call, request, callback ->
            callback.onResponse(call, responseFor(request, bodyJson, code))
        }
        return f.api to f.captured
    }

    private val statusBody = """{"session_id":"s1","etag":"e1"}"""
    private val diffBody = """{"path":"x","scope":"unstaged","kind":"text"}"""

    @Test
    fun getSessionGitStatus_omitsTheEtagWhenThereIsNone() = runTest {
        val (api, captured) = newApi(statusBody)

        val status = api.getSessionGitStatus("s1", etag = null)

        assertEquals("e1", status.etag)
        assertEquals("GET", captured.captured.method)
        assertEquals(
            "https://api.example.com/api/sessions/s1/git/status",
            captured.captured.url.toString(),
        )
    }

    @Test
    fun getSessionGitStatus_forwardsTheEtag() = runTest {
        val (api, captured) = newApi(statusBody)

        api.getSessionGitStatus("s1", etag = "3f0d4c1e")

        assertEquals(
            "https://api.example.com/api/sessions/s1/git/status?etag=3f0d4c1e",
            captured.captured.url.toString(),
        )
    }

    @Test
    fun getSessionGitDiff_encodesAnAwkwardPath_asOneQueryParameter() = runTest {
        // Everything a repo path can throw at a query string, in one value.
        val path = "src/new name#1&x=y+z/тест 100%.py"
        val (api, captured) = newApi(diffBody)

        api.getSessionGitDiff("s1", path, "unstaged", context = 3, origPath = null)

        val url = captured.captured.url
        assertEquals("/api/sessions/s1/git/diff", url.encodedPath)
        assertEquals(setOf("path", "scope", "context"), url.queryParameterNames)
        assertEquals(path, url.queryParameter("path"))
        assertEquals("unstaged", url.queryParameter("scope"))
        assertEquals("3", url.queryParameter("context"))
        assertNull(url.queryParameter("orig_path"))
        val encodedQuery = url.encodedQuery.orEmpty()
        for (raw in listOf(' ', '#', '+')) {
            assertFalse("raw '$raw' in $encodedQuery", raw in encodedQuery)
        }
    }

    @Test
    fun getSessionGitDiff_addsTheOriginalPathOnlyForRenames() = runTest {
        val (api, captured) = newApi(diffBody)

        api.getSessionGitDiff("s1", "src/new.py", "staged", context = 10, origPath = "src/old name.py")

        val url = captured.captured.url
        assertEquals("src/old name.py", url.queryParameter("orig_path"))
        assertEquals("10", url.queryParameter("context"))
        assertEquals(setOf("path", "scope", "context", "orig_path"), url.queryParameterNames)
    }

    // MARK: - Transport

    @Test
    fun cancellingTheCaller_cancelsThePendingCall() = runTest {
        // A superseded diff request must not keep downloading in the
        // background; the blocking `execute` path had no way to stop it.
        val f = newFixture { _, _, _ -> }
        every { f.call.cancel() } just Runs

        val job = launch {
            f.api.getSessionGitDiff("s1", "x", "unstaged", context = 3, origPath = null)
        }
        runCurrent()
        verify(exactly = 0) { f.call.cancel() }

        job.cancelAndJoin()

        verify(exactly = 1) { f.call.cancel() }
    }

    @Test
    fun aNon2xxAnswer_surfacesAsAnApiException_withTheBody() = runTest {
        val body = """{"detail":"Agent is offline"}"""
        val (api, _) = newApi(body, code = 503)

        try {
            api.getSessionGitStatus("s1", etag = null)
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(503, e.code)
            assertEquals(body, e.errorBody)
        }
    }

    @Test
    fun aTransportFailure_propagatesTheIOException() = runTest {
        val f = newFixture { call, _, callback -> callback.onFailure(call, IOException("reset by peer")) }

        try {
            f.api.getSessionGitStatus("s1", etag = null)
            fail("expected IOException")
        } catch (e: IOException) {
            assertEquals("reset by peer", e.message)
        }
    }

    @Test
    fun aBodyThatFailsMidRead_propagatesTheIOException() = runTest {
        // The body is consumed inside OkHttp's callback now; a read that dies
        // there has to reach the caller like a failure before the headers did.
        val failing = object : Source {
            override fun read(sink: Buffer, byteCount: Long): Long = throw IOException("stream reset")
            override fun timeout(): Timeout = Timeout.NONE
            override fun close() = Unit
        }.buffer().asResponseBody(jsonType)
        val f = newFixture { call, request, callback -> callback.onResponse(call, responseFor(request, failing)) }

        try {
            f.api.getSessionGitStatus("s1", etag = null)
            fail("expected IOException")
        } catch (e: IOException) {
            assertEquals("stream reset", e.message)
        }
    }
}
