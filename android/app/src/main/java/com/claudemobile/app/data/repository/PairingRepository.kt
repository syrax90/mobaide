package com.claudemobile.app.data.repository

import com.claudemobile.app.data.auth.ConnectionStore
import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.network.dto.PairingCompleteRequest
import com.claudemobile.app.data.network.dto.PairingCompleteResponse
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

class PairingRepository(
    private val refreshClient: OkHttpClient,
    private val tokenStore: TokenStore,
    private val sessionManager: SessionManager,
    private val connectionStore: ConnectionStore,
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val jsonMediaType = "application/json".toMediaType()

    suspend fun completePairing(
        serverUrl: String,
        pairingToken: String,
        deviceId: String,
        deviceName: String?,
    ): PairingCompleteResponse = withContext(Dispatchers.IO) {
        val body = json.encodeToString(
            PairingCompleteRequest(
                token = pairingToken,
                device_id = deviceId,
                device_name = deviceName,
            ),
        ).toRequestBody(jsonMediaType)

        val request = Request.Builder()
            .url("${serverUrl.trimEnd('/')}/api/pairing/complete")
            .post(body)
            .build()

        refreshClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                val errorBody = response.body?.string() ?: ""
                throw ApiException(response.code, errorBody)
            }
            val responseBody = response.body?.string()
                ?: throw ApiException(response.code, "Empty response body")
            json.decodeFromString<PairingCompleteResponse>(responseBody)
        }
    }

    fun savePairingCredentials(
        serverUrl: String,
        response: PairingCompleteResponse,
        deviceId: String,
    ) {
        tokenStore.saveRelayCredentials(
            serverUrl = serverUrl,
            agentId = response.agent_id,
            pairingId = response.pairing_id,
            deviceId = deviceId,
            accessToken = response.access_token,
            refreshToken = response.refresh_token,
        )
        sessionManager.markAgentConnected()

        // Propagate to ConnectionStore so multi-connection switching can restore this profile
        connectionStore.captureActiveProfile(
            tokenStore,
            label = "Relay: ${response.agent_id}",
        )
    }
}
