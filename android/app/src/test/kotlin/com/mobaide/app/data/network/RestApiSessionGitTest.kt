package com.mobaide.app.data.network

import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.dto.GitCommitRequest
import com.mobaide.app.data.network.dto.GitConflictResolveRequest
import com.mobaide.app.data.network.dto.GitConflictResponse
import com.mobaide.app.data.network.dto.GitConflictRestoreRequest
import com.mobaide.app.data.network.dto.GitConflictTakeRequest
import com.mobaide.app.data.network.dto.GitDiscardRequest
import com.mobaide.app.data.network.dto.GitPathsRequest
import com.mobaide.app.data.network.dto.GitPullRequest
import com.mobaide.app.data.network.dto.GitPushRequest
import com.mobaide.app.data.network.dto.GitRebaseRequest
import com.mobaide.app.data.network.dto.GitRebaseResponse
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
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
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
import org.junit.Assert.assertTrue
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

    /**
     * The mutating routes go through the plain `execute` path (a mutation the
     * agent has started runs to completion either way), stubbed as
     * `RestApiTest` does.
     */
    private fun newExecuteApi(bodyJson: String, code: Int = 200): Pair<RestApi, CapturingSlot<Request>> {
        val client = mockk<OkHttpClient>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getEffectiveBaseUrl() } returns "https://api.example.com"
        val captured = slot<Request>()
        val call = mockk<Call>()
        every { client.newCall(capture(captured)) } returns call
        every { call.execute() } answers { responseFor(captured.captured, bodyJson, code) }
        return RestApi(client, tokenStore) to captured
    }

    private fun Request.bodyJson(): JsonElement {
        val buffer = Buffer()
        body!!.writeTo(buffer)
        return Json.parseToJsonElement(buffer.readUtf8())
    }

    private val statusBody = """{"session_id":"s1","etag":"e1"}"""
    private val diffBody = """{"path":"x","scope":"unstaged","kind":"text"}"""
    private val okBody = """{"ok":true}"""

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

    // MARK: - Mutations

    @Test
    fun stageSessionGit_postsThePaths_asAJsonBody() = runTest {
        val (api, captured) = newExecuteApi(okBody)

        val resp = api.stageSessionGit("s1", GitPathsRequest(listOf("src/new name.py", "src/old.py")))

        assertTrue(resp.ok)
        val request = captured.captured
        assertEquals("POST", request.method)
        assertEquals("https://api.example.com/api/sessions/s1/git/stage", request.url.toString())
        assertEquals("application/json", request.body?.contentType()?.toString()?.substringBefore(';'))
        assertEquals(
            Json.parseToJsonElement("""{"paths":["src/new name.py","src/old.py"]}"""),
            request.bodyJson(),
        )
    }

    @Test
    fun unstageSessionGit_postsToItsOwnLeaf() = runTest {
        val (api, captured) = newExecuteApi(okBody)

        api.unstageSessionGit("s1", GitPathsRequest(listOf("keep.txt")))

        assertEquals("POST", captured.captured.method)
        assertEquals("https://api.example.com/api/sessions/s1/git/unstage", captured.captured.url.toString())
        assertEquals(Json.parseToJsonElement("""{"paths":["keep.txt"]}"""), captured.captured.bodyJson())
    }

    @Test
    fun discardSessionGit_carriesTheScope() = runTest {
        val (api, captured) = newExecuteApi(okBody)

        api.discardSessionGit("s1", GitDiscardRequest(listOf("notes.txt"), GitDiscardRequest.SCOPE_UNTRACKED))

        assertEquals("https://api.example.com/api/sessions/s1/git/discard", captured.captured.url.toString())
        assertEquals(
            Json.parseToJsonElement("""{"paths":["notes.txt"],"scope":"untracked"}"""),
            captured.captured.bodyJson(),
        )
    }

    @Test
    fun commitSessionGit_sendsTheFlags_andDecodesTheNewHead() = runTest {
        val (api, captured) = newExecuteApi(
            """{"oid":"4890423520f4dcd158b85374f44fc8819a813ac2","short_oid":"4890423","subject":"Fix"}""",
        )

        val resp = api.commitSessionGit("s1", GitCommitRequest("Fix\n\nWhy.", amend = true, no_verify = true))

        assertEquals("4890423", resp.short_oid)
        assertEquals("Fix", resp.subject)
        assertEquals("https://api.example.com/api/sessions/s1/git/commit", captured.captured.url.toString())
        assertEquals(
            Json.parseToJsonElement("""{"message":"Fix\n\nWhy.","amend":true,"no_verify":true}"""),
            captured.captured.bodyJson(),
        )
    }

    @Test
    fun commitSessionGit_leavesDefaultFlagsToTheAgent() = runTest {
        // The encoder drops defaults; the agent's model defaults both flags to
        // false, so a plain commit is `{"message": …}` on the wire.
        val (api, captured) = newExecuteApi("""{"oid":"a","short_oid":"a","subject":"x"}""")

        api.commitSessionGit("s1", GitCommitRequest("x"))

        assertEquals(Json.parseToJsonElement("""{"message":"x"}"""), captured.captured.bodyJson())
    }

    @Test
    fun getSessionGitCommitContext_readsTheNestedLeaf() = runTest {
        val (api, captured) = newApi(
            """{"author":{"name":"Ada","email":"ada@example.com"},"head":null}""",
        )

        val context = api.getSessionGitCommitContext("s1")

        assertEquals("GET", captured.captured.method)
        assertEquals(
            "https://api.example.com/api/sessions/s1/git/commit/context",
            captured.captured.url.toString(),
        )
        assertEquals("Ada", context.author?.name)
        assertNull(context.head)
    }

    @Test
    fun aRefusal_surfacesAsAnApiException_withThe409Body() = runTest {
        val body = """{"detail":{"code":"index_locked","message":"index.lock exists"}}"""
        val (api, _) = newExecuteApi(body, code = 409)

        try {
            api.stageSessionGit("s1", GitPathsRequest(listOf("a.txt")))
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(409, e.code)
            assertEquals(body, e.errorBody)
        }
    }
}

/** The fetch / pull / push job routes: start with a body per op, poll cancellably, cancel. */
@OptIn(ExperimentalCoroutinesApi::class)
class RestApiSessionGitJobsTest {

    private val jsonType = "application/json".toMediaType()
    private val jobBody = """{"job_id":"j1","session_id":"s1","op":"push","status":"running"}"""

    private fun responseFor(req: Request, bodyJson: String, code: Int = 200): Response = Response.Builder()
        .request(req)
        .protocol(Protocol.HTTP_1_1)
        .code(code)
        .message(if (code == 200) "OK" else "Error")
        .body(bodyJson.toResponseBody(jsonType))
        .build()

    private fun newExecuteApi(bodyJson: String): Pair<RestApi, CapturingSlot<Request>> {
        val client = mockk<OkHttpClient>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getEffectiveBaseUrl() } returns "https://api.example.com"
        val captured = slot<Request>()
        val call = mockk<Call>()
        every { client.newCall(capture(captured)) } returns call
        every { call.execute() } answers { responseFor(captured.captured, bodyJson) }
        return RestApi(client, tokenStore) to captured
    }

    private fun newEnqueueApi(bodyJson: String): Pair<RestApi, CapturingSlot<Request>> {
        val client = mockk<OkHttpClient>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getEffectiveBaseUrl() } returns "https://api.example.com"
        val captured = slot<Request>()
        val call = mockk<Call>()
        every { client.newCall(capture(captured)) } returns call
        every { call.enqueue(any()) } answers {
            firstArg<Callback>().onResponse(call, responseFor(captured.captured, bodyJson))
        }
        return RestApi(client, tokenStore) to captured
    }

    private fun Request.bodyJson(): JsonElement {
        val buffer = Buffer()
        body!!.writeTo(buffer)
        return Json.parseToJsonElement(buffer.readUtf8())
    }

    @Test
    fun fetchSessionGit_postsAnEmptyObject_andDecodesTheFirstSnapshot() = runTest {
        val (api, captured) = newExecuteApi(jobBody.replace("push", "fetch"))

        val job = api.fetchSessionGit("s1")

        assertEquals("POST", captured.captured.method)
        assertEquals("https://api.example.com/api/sessions/s1/git/fetch", captured.captured.url.toString())
        assertEquals(Json.parseToJsonElement("{}"), captured.captured.bodyJson())
        assertEquals("j1", job.job_id)
        assertEquals("fetch", job.op)
        assertTrue(job.running)
    }

    @Test
    fun pullSessionGit_carriesTheMode() = runTest {
        val (api, captured) = newExecuteApi(jobBody)

        api.pullSessionGit("s1", GitPullRequest(GitPullRequest.MODE_REBASE))

        assertEquals("https://api.example.com/api/sessions/s1/git/pull", captured.captured.url.toString())
        assertEquals(Json.parseToJsonElement("""{"mode":"rebase"}"""), captured.captured.bodyJson())
    }

    @Test
    fun pushSessionGit_carriesTheForceFlag_andLeavesTheDefaultToTheAgent() = runTest {
        // Like `commitSessionGit`, defaults are not serialised: an empty body
        // is what the agent's `body or {}` reads as `force=false`.
        val (api, captured) = newExecuteApi(jobBody)

        api.pushSessionGit("s1", GitPushRequest(force = true))
        assertEquals("https://api.example.com/api/sessions/s1/git/push", captured.captured.url.toString())
        assertEquals(Json.parseToJsonElement("""{"force":true}"""), captured.captured.bodyJson())

        api.pushSessionGit("s1", GitPushRequest())
        assertEquals(Json.parseToJsonElement("{}"), captured.captured.bodyJson())
    }

    @Test
    fun getSessionGitJob_readsTheJobUnderItsId_onTheCancellablePath() = runTest {
        val (api, captured) = newEnqueueApi(jobBody)

        val job = api.getSessionGitJob("s1", "j1")

        assertEquals("GET", captured.captured.method)
        assertEquals("https://api.example.com/api/sessions/s1/git/jobs/j1", captured.captured.url.toString())
        assertEquals("j1", job.job_id)
    }

    @Test
    fun cancelSessionGitJob_postsUnderTheJob() = runTest {
        val cancelled = """{"job_id":"j1","session_id":"s1","op":"push","status":"done","ok":false,"error_code":"cancelled"}"""
        val (api, captured) = newExecuteApi(cancelled)

        val job = api.cancelSessionGitJob("s1", "j1")

        assertEquals("POST", captured.captured.method)
        assertEquals(
            "https://api.example.com/api/sessions/s1/git/jobs/j1/cancel",
            captured.captured.url.toString(),
        )
        assertEquals(Json.parseToJsonElement("{}"), captured.captured.bodyJson())
        assertFalse(job.running)
        assertEquals("cancelled", job.error_code)
    }
}

/** The rebase routes: start with the base, then continue / skip / abort under `rebase/`. */
@OptIn(ExperimentalCoroutinesApi::class)
class RestApiSessionGitRebaseTest {

    private val jsonType = "application/json".toMediaType()

    private fun responseFor(req: Request, bodyJson: String): Response = Response.Builder()
        .request(req)
        .protocol(Protocol.HTTP_1_1)
        .code(200)
        .message("OK")
        .body(bodyJson.toResponseBody(jsonType))
        .build()

    private fun newExecuteApi(bodyJson: String): Pair<RestApi, CapturingSlot<Request>> {
        val client = mockk<OkHttpClient>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getEffectiveBaseUrl() } returns "https://api.example.com"
        val captured = slot<Request>()
        val call = mockk<Call>()
        every { client.newCall(capture(captured)) } returns call
        every { call.execute() } answers { responseFor(captured.captured, bodyJson) }
        return RestApi(client, tokenStore) to captured
    }

    private fun Request.bodyJson(): JsonElement {
        val buffer = Buffer()
        body!!.writeTo(buffer)
        return Json.parseToJsonElement(buffer.readUtf8())
    }

    @Test
    fun rebaseSessionGit_postsTheBase_andDecodesAStopWithItsProgress() = runTest {
        val (api, captured) = newExecuteApi(
            """{"outcome":"stopped","rebase":{"branch":"topic","onto_oid":"89abcdef","onto_short_oid":"89abcde",""" +
                """"onto_ref":"main","stopped_on":{"oid":"fedcba98","short_oid":"fedcba9","subject":"Fix"},""" +
                """"step":2,"total":5},"message":null}""",
        )

        val result = api.rebaseSessionGit("s1", GitRebaseRequest("origin/main"))

        assertEquals("POST", captured.captured.method)
        assertEquals("https://api.example.com/api/sessions/s1/git/rebase", captured.captured.url.toString())
        assertEquals(Json.parseToJsonElement("""{"onto":"origin/main"}"""), captured.captured.bodyJson())
        assertEquals(GitRebaseResponse.OUTCOME_STOPPED, result.outcome)
        assertEquals("topic", result.rebase?.branch)
        assertEquals("fedcba9", result.rebase?.stopped_on?.short_oid)
        assertEquals(2, result.rebase?.step)
        assertNull(result.message)
    }

    @Test
    fun stepSessionGitRebase_postsAnEmptyObject_underEachStepsOwnLeaf() = runTest {
        val (api, captured) = newExecuteApi("""{"outcome":"aborted","rebase":null,"message":null}""")

        for (step in listOf("continue", "skip", "abort")) {
            val result = api.stepSessionGitRebase("s1", step)

            assertEquals("POST", captured.captured.method)
            assertEquals(
                "https://api.example.com/api/sessions/s1/git/rebase/$step",
                captured.captured.url.toString(),
            )
            assertEquals(Json.parseToJsonElement("{}"), captured.captured.bodyJson())
            assertEquals(GitRebaseResponse.OUTCOME_ABORTED, result.outcome)
        }
    }

    @Test
    fun stepSessionGitRebase_decodesAnOutcomeANewerAgentMaySend() = runTest {
        val (api, _) = newExecuteApi("""{"outcome":"future_outcome"}""")

        val result = api.stepSessionGitRebase("s1", "continue")

        assertEquals("future_outcome", result.outcome)
        assertNull(result.rebase)
    }
}

/** The conflict resolver's routes: a cancellable read by path, then resolve / take / restore under `conflict/`. */
@OptIn(ExperimentalCoroutinesApi::class)
class RestApiSessionGitConflictTest {

    private val jsonType = "application/json".toMediaType()
    private val okBody = """{"ok":true}"""

    private fun responseFor(req: Request, bodyJson: String): Response = Response.Builder()
        .request(req)
        .protocol(Protocol.HTTP_1_1)
        .code(200)
        .message("OK")
        .body(bodyJson.toResponseBody(jsonType))
        .build()

    private fun newExecuteApi(bodyJson: String): Pair<RestApi, CapturingSlot<Request>> {
        val client = mockk<OkHttpClient>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getEffectiveBaseUrl() } returns "https://api.example.com"
        val captured = slot<Request>()
        val call = mockk<Call>()
        every { client.newCall(capture(captured)) } returns call
        every { call.execute() } answers { responseFor(captured.captured, bodyJson) }
        return RestApi(client, tokenStore) to captured
    }

    private fun newEnqueueApi(bodyJson: String): Pair<RestApi, CapturingSlot<Request>> {
        val client = mockk<OkHttpClient>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getEffectiveBaseUrl() } returns "https://api.example.com"
        val captured = slot<Request>()
        val call = mockk<Call>()
        every { client.newCall(capture(captured)) } returns call
        every { call.enqueue(any()) } answers {
            firstArg<Callback>().onResponse(call, responseFor(captured.captured, bodyJson))
        }
        return RestApi(client, tokenStore) to captured
    }

    private fun Request.bodyJson(): JsonElement {
        val buffer = Buffer()
        body!!.writeTo(buffer)
        return Json.parseToJsonElement(buffer.readUtf8())
    }

    @Test
    fun getSessionGitConflict_encodesAnAwkwardPath_asTheOneQueryParameter_andDecodesTheFile() = runTest {
        val path = "src/new name#1&x=y+z/тест 100%.py"
        val (api, captured) = newEnqueueApi(
            """{"path":"x","code":"DU","state":"rebasing","kind":"text","content":"kept\n","token":"t1",""" +
                """"markers":0,"has_base":false,"ours":{"label":"main","present":false},""" +
                """"theirs":{"label":null,"present":true},"byte_limit":262144}""",
        )

        val conflict = api.getSessionGitConflict("s1", path)

        val url = captured.captured.url
        assertEquals("GET", captured.captured.method)
        assertEquals("/api/sessions/s1/git/conflict", url.encodedPath)
        assertEquals(setOf("path"), url.queryParameterNames)
        assertEquals(path, url.queryParameter("path"))
        val encodedQuery = url.encodedQuery.orEmpty()
        for (raw in listOf(' ', '#', '+')) {
            assertFalse("raw '$raw' in $encodedQuery", raw in encodedQuery)
        }
        assertEquals(GitConflictResponse.KIND_TEXT, conflict.kind)
        assertEquals("kept\n", conflict.content)
        assertEquals("t1", conflict.token)
        assertFalse(conflict.ours.present)
        assertTrue(conflict.theirs.present)
        assertNull(conflict.theirs.label)
    }

    @Test
    fun resolveSessionGitConflict_postsADraft_withItsFalseFlag() = runTest {
        val (api, captured) = newExecuteApi(okBody)

        val resp = api.resolveSessionGitConflict(
            "s1",
            GitConflictResolveRequest("src/a b.py", "t1", "<<<<<<< x\nж\n", mark_resolved = false),
        )

        assertTrue(resp.ok)
        assertEquals("POST", captured.captured.method)
        assertEquals(
            "https://api.example.com/api/sessions/s1/git/conflict/resolve",
            captured.captured.url.toString(),
        )
        assertEquals(
            Json.parseToJsonElement(
                """{"path":"src/a b.py","token":"t1","content":"<<<<<<< x\nж\n","mark_resolved":false}""",
            ),
            captured.captured.bodyJson(),
        )
    }

    @Test
    fun resolveSessionGitConflict_leavesTheApplyDefaultToTheAgent() = runTest {
        val (api, captured) = newExecuteApi(okBody)

        api.resolveSessionGitConflict("s1", GitConflictResolveRequest("a.py", "t1", "x\n"))

        assertEquals(
            Json.parseToJsonElement("""{"path":"a.py","token":"t1","content":"x\n"}"""),
            captured.captured.bodyJson(),
        )
    }

    @Test
    fun takeSessionGitConflict_postsTheSide_underItsOwnLeaf() = runTest {
        val (api, captured) = newExecuteApi(okBody)

        api.takeSessionGitConflict("s1", GitConflictTakeRequest("docs/old.md", GitConflictResponse.SIDE_THEIRS))

        assertEquals("POST", captured.captured.method)
        assertEquals(
            "https://api.example.com/api/sessions/s1/git/conflict/take",
            captured.captured.url.toString(),
        )
        assertEquals(
            Json.parseToJsonElement("""{"path":"docs/old.md","side":"theirs"}"""),
            captured.captured.bodyJson(),
        )
    }

    @Test
    fun restoreSessionGitConflict_carriesTheStyleAndToken_onlyWhenTheyAreSet() = runTest {
        val (api, captured) = newExecuteApi(okBody)

        api.restoreSessionGitConflict(
            "s1", GitConflictRestoreRequest("src/login.py", GitConflictRestoreRequest.STYLE_DIFF3, "t1"),
        )
        val startOver = captured.captured.bodyJson()
        val url = captured.captured.url.toString()
        api.restoreSessionGitConflict("s1", GitConflictRestoreRequest("src/session.py"))

        assertEquals("https://api.example.com/api/sessions/s1/git/conflict/restore", url)
        assertEquals(
            Json.parseToJsonElement("""{"path":"src/login.py","style":"diff3","token":"t1"}"""),
            startOver,
        )
        assertEquals(Json.parseToJsonElement("""{"path":"src/session.py"}"""), captured.captured.bodyJson())
    }
}
