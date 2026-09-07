package com.mobaide.app.data.network

import com.mobaide.app.data.auth.TokenStore
import io.mockk.CapturingSlot
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
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * URL construction for the session-scoped git Changes routes. Same mocked
 * [OkHttpClient] / [Call] arrangement as `RestApiTest`: the [Request] handed
 * to `newCall` is captured and its URL inspected.
 */
class RestApiSessionGitTest {

    private val jsonType = "application/json".toMediaType()

    private fun responseFor(req: Request, bodyJson: String): Response = Response.Builder()
        .request(req)
        .protocol(Protocol.HTTP_1_1)
        .code(200)
        .message("OK")
        .body(bodyJson.toResponseBody(jsonType))
        .build()

    private fun newApi(bodyJson: String): Pair<RestApi, CapturingSlot<Request>> {
        val client = mockk<OkHttpClient>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getEffectiveBaseUrl() } returns "https://api.example.com"
        val captured = slot<Request>()
        val call = mockk<Call>()
        every { client.newCall(capture(captured)) } returns call
        every { call.execute() } answers { responseFor(captured.captured, bodyJson) }
        return RestApi(client, tokenStore) to captured
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
}
