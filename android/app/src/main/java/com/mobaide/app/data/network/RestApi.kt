package com.mobaide.app.data.network

import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.dto.AutoScanListResponse
import com.mobaide.app.data.network.dto.ClaudeOptionsModel
import com.mobaide.app.data.network.dto.CreateSessionRequest
import com.mobaide.app.data.network.dto.ForceUpdateResponse
import com.mobaide.app.data.network.dto.GitDeleteRequest
import com.mobaide.app.data.network.dto.GitDeleteResponse
import com.mobaide.app.data.network.dto.GitItemsResponse
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.network.dto.ProjectOrderRequest
import com.mobaide.app.data.network.dto.ProjectUpdate
import com.mobaide.app.data.network.dto.ProjectWrite
import com.mobaide.app.data.network.dto.ScrollbackResponse
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.network.dto.RunNowRequest
import com.mobaide.app.data.network.dto.RunNowResponse
import com.mobaide.app.data.network.dto.StartScanRequest
import com.mobaide.app.data.network.dto.StopScanRequest
import com.mobaide.app.data.network.dto.TokenRequest
import com.mobaide.app.data.network.dto.TokenResponse
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.URLEncoder

class RestApi(
    private val client: OkHttpClient,
    private val tokenStore: TokenStore,
) {
    private val json = Json { ignoreUnknownKeys = true }

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

    private fun encodePathSegment(name: String): String =
        URLEncoder.encode(name, "UTF-8").replace("+", "%20")

    suspend fun getGitItems(projectName: String): GitItemsResponse {
        val request = Request.Builder()
            .url("${baseUrl()}/api/projects/${encodePathSegment(projectName)}/git/items")
            .get()
            .build()
        return execute(request)
    }

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
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    val errorBody = response.body?.string() ?: ""
                    throw ApiException(response.code, errorBody)
                }
                val body = response.body?.string()
                    ?: throw ApiException(response.code, "Empty response body")
                json.decodeFromString<T>(body)
            }
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
