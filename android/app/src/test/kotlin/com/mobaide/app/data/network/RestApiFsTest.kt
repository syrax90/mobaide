package com.mobaide.app.data.network

import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.dto.FsDirsResponse
import io.mockk.CapturingSlot
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test

/**
 * URL construction and error surfacing of `GET /api/fs/dirs`. Same mocked
 * [OkHttpClient] / [Call] `enqueue` arrangement as `RestApiSessionGitTest`:
 * the route is cancellable, so the stub answers through the [Callback].
 */
class RestApiFsTest {

    private fun newApi(bodyJson: String, code: Int = 200): Pair<RestApi, CapturingSlot<Request>> {
        val client = mockk<OkHttpClient>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getEffectiveBaseUrl() } returns "https://api.example.com"
        val captured = slot<Request>()
        val call = mockk<Call>()
        every { client.newCall(capture(captured)) } returns call
        every { call.enqueue(any()) } answers {
            val response = Response.Builder()
                .request(captured.captured)
                .protocol(Protocol.HTTP_1_1)
                .code(code)
                .message(if (code == 200) "OK" else "Error")
                .body(bodyJson.toResponseBody("application/json".toMediaType()))
                .build()
            firstArg<Callback>().onResponse(call, response)
        }
        return RestApi(client, tokenStore) to captured
    }

    private val listingBody = """{"path":"/home/me","dirs":["code"],"truncated":false}"""

    @Test
    fun listDirs_sendsThePathAlone_whenThereIsNoQ() = runTest {
        val (api, captured) = newApi(listingBody)

        val listing = api.listDirs("~/", q = null)

        assertEquals(FsDirsResponse("/home/me", listOf("code"), truncated = false), listing)
        val url = captured.captured.url
        assertEquals("GET", captured.captured.method)
        assertEquals("/api/fs/dirs", url.encodedPath)
        assertEquals(setOf("path"), url.queryParameterNames)
        assertEquals("~/", url.queryParameter("path"))
        assertNull(url.queryParameter("q"))
    }

    @Test
    fun listDirs_encodesAnAwkwardPathAndQ_asOneParameterEach() = runTest {
        val (api, captured) = newApi(listingBody)
        val path = "C:\\My Docs & more\\#1+2\\тест\\"

        api.listDirs(path, q = "a&b c")

        val url = captured.captured.url
        assertEquals(setOf("path", "q"), url.queryParameterNames)
        assertEquals(path, url.queryParameter("path"))
        assertEquals("a&b c", url.queryParameter("q"))
    }

    @Test
    fun listDirs_surfacesTheStatus_soTheRepositoryCanTellMissingFromBusy() = runTest {
        val (api, _) = newApi("""{"detail":"No such directory: /x"}""", code = 404)

        try {
            api.listDirs("/x/", q = null)
            fail("a 404 must throw")
        } catch (e: ApiException) {
            assertEquals(404, e.code)
        }
    }
}
