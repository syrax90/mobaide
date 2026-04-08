package com.claudemobile.app.data.network

import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.network.dto.AutoScanListResponse
import com.claudemobile.app.data.network.dto.CreateSessionRequest
import com.claudemobile.app.data.network.dto.ProjectInfo
import com.claudemobile.app.data.network.dto.ScrollbackResponse
import com.claudemobile.app.data.network.dto.SessionResponse
import com.claudemobile.app.data.network.dto.RunNowRequest
import com.claudemobile.app.data.network.dto.RunNowResponse
import com.claudemobile.app.data.network.dto.StartScanRequest
import com.claudemobile.app.data.network.dto.StopScanRequest
import com.claudemobile.app.data.network.dto.TokenRequest
import com.claudemobile.app.data.network.dto.TokenResponse
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

class RestApi(
    private val client: OkHttpClient,
    private val tokenStore: TokenStore,
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val jsonMediaType = "application/json".toMediaType()

    private fun baseUrl(): String = tokenStore.getServerUrl()
        ?: throw IllegalStateException("Server URL not configured")

    // Auth

    suspend fun login(serverUrl: String, username: String, password: String): TokenResponse {
        val body = json.encodeToString(TokenRequest(username, password))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${serverUrl.trimEnd('/')}/api/auth/token")
            .post(body)
            .build()
        return execute(request)
    }

    suspend fun refreshToken(refreshToken: String): TokenResponse {
        val body = """{"refresh_token":"$refreshToken"}""".toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/auth/refresh")
            .post(body)
            .build()
        return execute(request)
    }

    // Sessions

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

    // Projects (CLA-12 / CLA-14)

    suspend fun getProjects(): List<ProjectInfo> {
        val request = Request.Builder()
            .url("${baseUrl()}/api/projects")
            .get()
            .build()
        return execute(request)
    }

    // Auto Scan

    suspend fun getAutoScanList(): AutoScanListResponse {
        val request = Request.Builder()
            .url("${baseUrl()}/api/autoscan/list")
            .get()
            .build()
        return execute(request)
    }

    suspend fun startAutoScan(projectName: String) {
        val body = json.encodeToString(StartScanRequest(projectName))
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

    // Helpers

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
