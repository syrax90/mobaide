package com.mobaide.app.data.network

import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.dto.AutoScanListResponse
import com.mobaide.app.data.network.dto.ChatStoredSessionsResponse
import com.mobaide.app.data.network.dto.ClaudeOptionsModel
import com.mobaide.app.data.network.dto.CodexLoginRequest
import com.mobaide.app.data.network.dto.CodexLoginResponse
import com.mobaide.app.data.network.dto.CreateSessionRequest
import com.mobaide.app.data.network.dto.ForceUpdateResponse
import com.mobaide.app.data.network.dto.FsDirsResponse
import com.mobaide.app.data.network.dto.GitCommitContextResponse
import com.mobaide.app.data.network.dto.GitCommitRequest
import com.mobaide.app.data.network.dto.GitCommitResponse
import com.mobaide.app.data.network.dto.GitConflictResolveRequest
import com.mobaide.app.data.network.dto.GitConflictResponse
import com.mobaide.app.data.network.dto.GitConflictRestoreRequest
import com.mobaide.app.data.network.dto.GitConflictTakeRequest
import com.mobaide.app.data.network.dto.GitDeleteRequest
import com.mobaide.app.data.network.dto.GitCheckoutRequest
import com.mobaide.app.data.network.dto.GitBranchResponse
import com.mobaide.app.data.network.dto.GitBranchCreateRequest
import com.mobaide.app.data.network.dto.GitDeleteResponse
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.data.network.dto.GitDiscardRequest
import com.mobaide.app.data.network.dto.GitItemsResponse
import com.mobaide.app.data.network.dto.GitJobResponse
import com.mobaide.app.data.network.dto.GitMutationResponse
import com.mobaide.app.data.network.dto.GitPathsRequest
import com.mobaide.app.data.network.dto.GitPullRequest
import com.mobaide.app.data.network.dto.GitPushRequest
import com.mobaide.app.data.network.dto.GitRebaseRequest
import com.mobaide.app.data.network.dto.GitRebaseResponse
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.LinearLabelDto
import com.mobaide.app.data.network.dto.LinearStateDto
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.network.dto.ProjectOrderRequest
import com.mobaide.app.data.network.dto.ProjectUpdate
import com.mobaide.app.data.network.dto.ProjectWrite
import com.mobaide.app.data.network.dto.RunNowRequest
import com.mobaide.app.data.network.dto.RunNowResponse
import com.mobaide.app.data.network.dto.ScrollbackResponse
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.network.dto.StartScanRequest
import com.mobaide.app.data.network.dto.StopScanRequest
import com.mobaide.app.data.network.dto.TokenRequest
import com.mobaide.app.data.network.dto.TokenResponse
import com.mobaide.app.data.network.dto.VersionInfoResponse
import com.mobaide.app.data.network.dto.VoiceDictationAudioRequest
import com.mobaide.app.data.network.dto.VoiceDictationAudioResponse
import com.mobaide.app.data.network.dto.VoiceDictationCreateRequest
import com.mobaide.app.data.network.dto.VoiceDictationFinishRequest
import com.mobaide.app.data.network.dto.VoiceDictationResponse
import com.mobaide.app.data.network.dto.VoiceDictationResultResponse
import com.mobaide.app.data.network.dto.VoiceInstallRequest
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.net.URLEncoder
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

class RestApi(
    private val client: OkHttpClient,
    private val tokenStore: TokenStore,
) {
    // coerceInputValues no longer touches unknown SessionKind strings (they
    // decode verbatim via SessionKindSerializer's STRING descriptor, proto-v7);
    // it is kept so an explicit `"kind": null` still falls back to
    // SessionResponse.kind's default instead of throwing.
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    // Dedicated encoder for `ProjectUpdate`: `explicitNulls = false` +
    // `encodeDefaults = false` drop null/default fields from the wire, so the
    // server's "omitted = keep existing value" three-state semantics (rename,
    // linear_api_key, conda_env, default_flags) work correctly. Do NOT use
    // this for `ProjectWrite`: we want `default_flags = emptyMap()` to be
    // encoded as `"default_flags": {}`, not dropped.
    private val jsonForUpdate = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        encodeDefaults = false
    }
    private val jsonMediaType = "application/json".toMediaType()

    private fun baseUrl(): String = tokenStore.getEffectiveBaseUrl()
        ?: throw IllegalStateException("Agent URL not configured")

    suspend fun login(agentUrl: String, username: String, password: String): TokenResponse {
        val body = json.encodeToString(TokenRequest(username, password))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${agentUrl.trimEnd('/')}/api/auth/token")
            .post(body)
            .build()
        return execute(request)
    }

    suspend fun forceAgentUpdate(): ForceUpdateResponse {
        val body = "".toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/agent/update/force_now")
            .post(body)
            .build()
        return execute(request)
    }

    suspend fun getAgentVersion(): VersionInfoResponse {
        val request = Request.Builder()
            .url("${baseUrl()}/api/version")
            .get()
            .build()
        return execute(request)
    }

    // Note: token refresh is handled exclusively by SessionManager, which
    // uses a dedicated bare OkHttpClient with no AuthInterceptor and
    // single-flight coalesces concurrent callers through an
    // AtomicReference<Deferred<RefreshResult>>. A RestApi.refreshToken
    // method would go through the authenticated client and bypass that
    // coordination — exactly the second independent refresh path that
    // produced the original crash. Do not re-introduce it here.

    suspend fun createSession(createRequest: CreateSessionRequest): SessionResponse {
        val body = json.encodeToString(createRequest).toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/sessions")
            .post(body)
            .build()
        return execute(request)
    }

    suspend fun listSessions(): List<SessionResponse> {
        val request = Request.Builder()
            .url("${baseUrl()}/api/sessions")
            .get()
            .build()
        return execute(request)
    }

    /** Cancellable: the resume picker supersedes a page when the folder scope changes. */
    suspend fun listChatSessions(kind: String, cwd: String?, cursor: String?): ChatStoredSessionsResponse {
        val url = baseUrl().toHttpUrl().newBuilder()
            .addPathSegment("api")
            .addPathSegment("chat")
            .addPathSegment(kind)
            .addPathSegment("sessions")
            .apply {
                if (cwd != null) addQueryParameter("cwd", cwd)
                if (cursor != null) addQueryParameter("cursor", cursor)
            }
            .build()
        return executeCancellable(Request.Builder().url(url).get().build())
    }

    /** Authenticate the host's Codex CLI with an OpenAI API key (stdin-piped,
     *  never persisted by app or agent). */
    suspend fun codexLogin(apiKey: String): CodexLoginResponse {
        val body = json.encodeToString(CodexLoginRequest(apiKey)).toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/codex/login")
            .post(body)
            .build()
        return execute(request)
    }

    suspend fun deleteSession(sessionId: String, force: Boolean = false): Unit {
        val url = "${baseUrl()}/api/sessions/$sessionId" +
            if (force) "?force=true" else ""
        val request = Request.Builder()
            .url(url)
            .delete()
            .build()
        executeRaw(request)
    }

    suspend fun getScrollback(sessionId: String, lines: Int = 1000): ScrollbackResponse {
        val request = Request.Builder()
            .url("${baseUrl()}/api/sessions/$sessionId/scrollback?lines=$lines")
            .get()
            .build()
        return execute(request)
    }

    suspend fun getProjects(): List<ProjectInfo> {
        val request = Request.Builder()
            .url("${baseUrl()}/api/projects")
            .get()
            .build()
        return execute(request)
    }

    suspend fun createProject(body: ProjectWrite): ProjectInfo {
        val payload = json.encodeToString(body).toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/projects")
            .post(payload)
            .build()
        return execute(request)
    }

    suspend fun updateProject(currentName: String, body: ProjectUpdate): ProjectInfo {
        val payload = jsonForUpdate.encodeToString(body).toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/projects/${encodePathSegment(currentName)}")
            .put(payload)
            .build()
        return execute(request)
    }

    suspend fun deleteProject(name: String) {
        val request = Request.Builder()
            .url("${baseUrl()}/api/projects/${encodePathSegment(name)}")
            .delete()
            .build()
        executeRaw(request)
    }

    suspend fun reorderProjects(body: ProjectOrderRequest): List<ProjectInfo> {
        val payload = json.encodeToString(body).toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/projects/order")
            .patch(payload)
            .build()
        return execute(request)
    }

    suspend fun getLinearLabels(projectName: String): List<LinearLabelDto> {
        val request = Request.Builder()
            .url("${baseUrl()}/api/projects/${encodePathSegment(projectName)}/linear/labels")
            .get()
            .build()
        return execute(request)
    }

    suspend fun getLinearStates(projectName: String): List<LinearStateDto> {
        val request = Request.Builder()
            .url("${baseUrl()}/api/projects/${encodePathSegment(projectName)}/linear/states")
            .get()
            .build()
        return execute(request)
    }

    private fun encodePathSegment(name: String): String =
        URLEncoder.encode(name, "UTF-8").replace("+", "%20")

    // Always asks for the remote-tracking rows: the flag shields *older
    // clients* from a kind they would offer for delete, and an older agent
    // ignores it.
    suspend fun getGitItems(projectName: String): GitItemsResponse {
        val url = projectGitUrl(projectName, "items")
            .addQueryParameter("remote", "true")
            .build()
        return execute(Request.Builder().url(url).get().build())
    }

    suspend fun createGitBranch(
        projectName: String,
        body: GitBranchCreateRequest,
    ): GitBranchResponse {
        val payload = json.encodeToString(body).toRequestBody(jsonMediaType)
        val url = projectGitUrl(projectName, "branches").build()
        return execute(Request.Builder().url(url).post(payload).build())
    }

    suspend fun checkoutGitRef(
        projectName: String,
        body: GitCheckoutRequest,
    ): GitBranchResponse {
        val payload = json.encodeToString(body).toRequestBody(jsonMediaType)
        val url = projectGitUrl(projectName, "checkout").build()
        return execute(Request.Builder().url(url).post(payload).build())
    }

    /** Cancellable: the path field supersedes a listing as soon as the user moves on. */
    suspend fun listDirs(path: String, q: String?): FsDirsResponse {
        val url = baseUrl().toHttpUrl().newBuilder()
            .addPathSegment("api")
            .addPathSegment("fs")
            .addPathSegment("dirs")
            .addQueryParameter("path", path)
            .apply { if (q != null) addQueryParameter("q", q) }
            .build()
        return executeCancellable(Request.Builder().url(url).get().build())
    }

    private fun projectGitUrl(projectName: String, leaf: String): HttpUrl.Builder =
        baseUrl().toHttpUrl().newBuilder()
            .addPathSegment("api")
            .addPathSegment("projects")
            .addPathSegment(projectName)
            .addPathSegment("git")
            .addPathSegment(leaf)

    suspend fun deleteGitItems(
        projectName: String,
        body: GitDeleteRequest,
    ): GitDeleteResponse {
        val payload = json.encodeToString(body).toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/projects/${encodePathSegment(projectName)}/git/items:delete")
            .post(payload)
            .build()
        return execute(request)
    }

    suspend fun getSessionGitStatus(sessionId: String, etag: String?): GitStatusResponse {
        val url = sessionGitUrl(sessionId, "status")
            .apply { if (etag != null) addQueryParameter("etag", etag) }
            .build()
        return executeCancellable(Request.Builder().url(url).get().build())
    }

    suspend fun getSessionGitDiff(
        sessionId: String,
        path: String,
        scope: String,
        context: Int,
        origPath: String?,
    ): GitDiffResponse {
        val url = sessionGitUrl(sessionId, "diff")
            .addQueryParameter("path", path)
            .addQueryParameter("scope", scope)
            .addQueryParameter("context", context.toString())
            .apply { if (origPath != null) addQueryParameter("orig_path", origPath) }
            .build()
        return executeCancellable(Request.Builder().url(url).get().build())
    }

    suspend fun stageSessionGit(sessionId: String, body: GitPathsRequest): GitMutationResponse =
        postSessionGit(sessionId, "stage", json.encodeToString(body))

    suspend fun unstageSessionGit(sessionId: String, body: GitPathsRequest): GitMutationResponse =
        postSessionGit(sessionId, "unstage", json.encodeToString(body))

    suspend fun discardSessionGit(sessionId: String, body: GitDiscardRequest): GitMutationResponse =
        postSessionGit(sessionId, "discard", json.encodeToString(body))

    suspend fun getSessionGitCommitContext(sessionId: String): GitCommitContextResponse {
        val url = sessionGitUrl(sessionId, "commit").addPathSegment("context").build()
        return executeCancellable(Request.Builder().url(url).get().build())
    }

    suspend fun commitSessionGit(sessionId: String, body: GitCommitRequest): GitCommitResponse =
        postSessionGit(sessionId, "commit", json.encodeToString(body))

    suspend fun fetchSessionGit(sessionId: String): GitJobResponse =
        postSessionGit(sessionId, "fetch", "{}")

    suspend fun pullSessionGit(sessionId: String, body: GitPullRequest): GitJobResponse =
        postSessionGit(sessionId, "pull", json.encodeToString(body))

    suspend fun pushSessionGit(sessionId: String, body: GitPushRequest): GitJobResponse =
        postSessionGit(sessionId, "push", json.encodeToString(body))

    suspend fun rebaseSessionGit(sessionId: String, body: GitRebaseRequest): GitRebaseResponse =
        postSessionGit(sessionId, "rebase", json.encodeToString(body))

    /** `step` is `continue`, `skip` or `abort`: `POST …/git/rebase/<step>`. */
    suspend fun stepSessionGitRebase(sessionId: String, step: String): GitRebaseResponse =
        postJson(sessionGitUrl(sessionId, "rebase").addPathSegment(step).build(), "{}")

    suspend fun getSessionGitConflict(sessionId: String, path: String): GitConflictResponse {
        val url = sessionGitUrl(sessionId, "conflict").addQueryParameter("path", path).build()
        return executeCancellable(Request.Builder().url(url).get().build())
    }

    suspend fun resolveSessionGitConflict(
        sessionId: String,
        body: GitConflictResolveRequest,
    ): GitMutationResponse = postSessionGitConflict(sessionId, "resolve", json.encodeToString(body))

    suspend fun takeSessionGitConflict(
        sessionId: String,
        body: GitConflictTakeRequest,
    ): GitMutationResponse = postSessionGitConflict(sessionId, "take", json.encodeToString(body))

    suspend fun restoreSessionGitConflict(
        sessionId: String,
        body: GitConflictRestoreRequest,
    ): GitMutationResponse = postSessionGitConflict(sessionId, "restore", json.encodeToString(body))

    private suspend fun postSessionGitConflict(
        sessionId: String,
        leaf: String,
        payload: String,
    ): GitMutationResponse =
        postJson(sessionGitUrl(sessionId, "conflict").addPathSegment(leaf).build(), payload)

    suspend fun getSessionGitJob(sessionId: String, jobId: String): GitJobResponse {
        val url = sessionGitUrl(sessionId, "jobs").addPathSegment(jobId).build()
        return executeCancellable(Request.Builder().url(url).get().build())
    }

    suspend fun cancelSessionGitJob(sessionId: String, jobId: String): GitJobResponse {
        val url = sessionGitUrl(sessionId, "jobs")
            .addPathSegment(jobId)
            .addPathSegment("cancel")
            .build()
        return postJson(url, "{}")
    }

    // Plain `execute`, not the cancellable path: a mutation the agent has
    // already started runs to completion either way, so an abandoned caller
    // would only lose the answer, never the effect.
    private suspend inline fun <reified T> postSessionGit(
        sessionId: String,
        leaf: String,
        payload: String,
    ): T = postJson(sessionGitUrl(sessionId, leaf).build(), payload)

    private suspend inline fun <reified T> postJson(url: HttpUrl, payload: String): T {
        val request = Request.Builder()
            .url(url)
            .post(payload.toRequestBody(jsonMediaType))
            .build()
        return execute(request)
    }

    // HttpUrl.Builder rather than string concatenation: repo-relative paths carry
    // spaces, `#`, `%` and non-ASCII, and addQueryParameter encodes them all.
    private fun sessionGitUrl(sessionId: String, leaf: String): HttpUrl.Builder =
        baseUrl().toHttpUrl().newBuilder()
            .addPathSegment("api")
            .addPathSegment("sessions")
            .addPathSegment(sessionId)
            .addPathSegment("git")
            .addPathSegment(leaf)

    suspend fun getVoiceStatus(): VoiceStatusResponse =
        executeCancellable(Request.Builder().url(voiceUrl("status").build()).get().build())

    suspend fun installVoice(): VoiceStatusResponse =
        postJson(voiceUrl("install").build(), json.encodeToString(VoiceInstallRequest()))

    suspend fun cancelVoiceInstall(): VoiceStatusResponse =
        postJson(voiceUrl("install", "cancel").build(), "{}")

    suspend fun removeVoice(): VoiceStatusResponse =
        execute(Request.Builder().url(voiceUrl("model").build()).delete().build())

    // Not cancellable on purpose: a dictation the agent opened must come back
    // with its id so it can be deleted, or it would hold one of the agent's
    // two slots until it expires.
    suspend fun createVoiceDictation(sessionId: String?): VoiceDictationResponse =
        postJson(
            voiceUrl("dictations").build(),
            json.encodeToString(VoiceDictationCreateRequest(session_id = sessionId)),
        )

    suspend fun appendVoiceAudio(
        dictationId: String,
        body: VoiceDictationAudioRequest,
    ): VoiceDictationAudioResponse = executeCancellable(
        Request.Builder()
            .url(voiceUrl("dictations", dictationId, "audio").build())
            .post(json.encodeToString(body).toRequestBody(jsonMediaType))
            .build(),
    )

    suspend fun finishVoiceDictation(dictationId: String, lastSeq: Int): VoiceDictationResultResponse =
        executeCancellable(
            Request.Builder()
                .url(voiceUrl("dictations", dictationId, "finish").build())
                .post(
                    json.encodeToString(VoiceDictationFinishRequest(last_seq = lastSeq))
                        .toRequestBody(jsonMediaType),
                )
                .build(),
        )

    suspend fun deleteVoiceDictation(dictationId: String) =
        executeRaw(Request.Builder().url(voiceUrl("dictations", dictationId).build()).delete().build())

    private fun voiceUrl(vararg segments: String): HttpUrl.Builder =
        baseUrl().toHttpUrl().newBuilder()
            .addPathSegment("api")
            .addPathSegment("voice")
            .apply { segments.forEach { addPathSegment(it) } }

    suspend fun getAutoScanList(): AutoScanListResponse {
        val request = Request.Builder()
            .url("${baseUrl()}/api/autoscan/list")
            .get()
            .build()
        return execute(request)
    }

    suspend fun startAutoScan(projectName: String, claudeOptions: ClaudeOptionsModel? = null) {
        val body = json.encodeToString(StartScanRequest(projectName, claudeOptions))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/autoscan/start")
            .post(body)
            .build()
        executeRaw(request)
    }

    suspend fun stopAutoScan(projectName: String) {
        val body = json.encodeToString(StopScanRequest(projectName))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/autoscan/stop")
            .post(body)
            .build()
        executeRaw(request)
    }

    suspend fun runAutoScanNow(projectName: String): RunNowResponse {
        val body = json.encodeToString(RunNowRequest(projectName))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/autoscan/run-now")
            .post(body)
            .build()
        return execute(request)
    }

    private suspend inline fun <reified T> execute(request: Request): T =
        withContext(Dispatchers.IO) {
            client.newCall(request).execute().use { decodeBody<T>(it) }
        }

    /**
     * [execute] for callers that supersede requests (`transformLatest`,
     * `WhileSubscribed` restarts): the blocking `Call.execute` ignores coroutine
     * cancellation, so an obsolete git diff would otherwise be downloaded and
     * decoded in full — and the caller made to wait for it — before the
     * replacement could start. Cancelling the coroutine now cancels the call,
     * an in-progress body read included, because the body is consumed on
     * OkHttp's thread inside the callback rather than handed across a
     * dispatcher hop where a cancellation could strand it unclosed.
     */
    private suspend inline fun <reified T> executeCancellable(request: Request): T =
        client.newCall(request).await { decodeBody<T>(it) }

    private inline fun <reified T> decodeBody(response: Response): T {
        if (!response.isSuccessful) {
            val errorBody = response.body?.string() ?: ""
            throw ApiException(response.code, errorBody)
        }
        val body = response.body?.string()
            ?: throw ApiException(response.code, "Empty response body")
        return json.decodeFromString<T>(body)
    }

    private suspend fun <T> Call.await(transform: (Response) -> T): T =
        suspendCancellableCoroutine { cont ->
            cont.invokeOnCancellation { cancel() }
            enqueue(object : Callback {
                override fun onResponse(call: Call, response: Response) {
                    runCatching { response.use(transform) }.fold(
                        onSuccess = { cont.resume(it) },
                        onFailure = { if (!cont.isCancelled) cont.resumeWithException(it) },
                    )
                }

                override fun onFailure(call: Call, e: IOException) {
                    if (!cont.isCancelled) cont.resumeWithException(e)
                }
            })
        }

    private suspend fun executeRaw(request: Request) = withContext(Dispatchers.IO) {
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                val errorBody = response.body?.string() ?: ""
                throw ApiException(response.code, errorBody)
            }
        }
    }
}

class ApiException(val code: Int, val errorBody: String) :
    Exception("HTTP $code: $errorBody")
