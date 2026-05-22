package com.mobaide.app.data.network

import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.CancelAccountDeletionResponse
import com.mobaide.app.data.network.dto.CreateAgentRequest
import com.mobaide.app.data.network.dto.CreateAgentResponse
import com.mobaide.app.data.network.dto.CreateTemplateRequest
import com.mobaide.app.data.network.dto.DeleteAccountRequest
import com.mobaide.app.data.network.dto.DeviceResponse
import com.mobaide.app.data.network.dto.ForgotPasswordRequest
import com.mobaide.app.data.network.dto.ForgotPasswordResponse
import com.mobaide.app.data.network.dto.GoogleAuthRequest
import com.mobaide.app.data.network.dto.MobileTokenRequest
import com.mobaide.app.data.network.dto.MobileTokenResponse
import com.mobaide.app.data.network.dto.PairingClaimRequest
import com.mobaide.app.data.network.dto.PairingClaimResponse
import com.mobaide.app.data.network.dto.ReRegisterAgentResponse
import com.mobaide.app.data.network.dto.ResendVerificationRequest
import com.mobaide.app.data.network.dto.ResendVerificationResponse
import com.mobaide.app.data.network.dto.ResetPasswordRequest
import com.mobaide.app.data.network.dto.ResetPasswordResponse
import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.data.network.dto.UpdateTemplateRequest
import com.mobaide.app.data.network.dto.UserAuthResponse
import com.mobaide.app.data.network.dto.UserLoginRequest
import com.mobaide.app.data.network.dto.UserRegisterRequest
import com.mobaide.app.data.network.dto.VerifyEmailRequest
import com.mobaide.app.data.network.dto.VersionInfoResponse
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * HTTP client for **central server** user-auth endpoints (register, login,
 * Google OAuth). Uses a bare [OkHttpClient] without [com.mobaide.app.data.auth.AuthInterceptor]
 * because user-auth calls do not carry agent-connection tokens.
 *
 * This is the user-identity counterpart to [RestApi], which targets per-agent
 * URLs for session management.
 */
class CentralApi(
    private val client: OkHttpClient,
    private val centralServerUrl: String,
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val jsonMediaType = "application/json".toMediaType()

    private fun baseUrl(): String = centralServerUrl.trimEnd('/')

    suspend fun getVersion(): VersionInfoResponse {
        val request = Request.Builder()
            .url("${baseUrl()}/api/version")
            .get()
            .build()
        return execute(request)
    }

    suspend fun register(email: String, password: String): UserAuthResponse {
        val body = json.encodeToString(UserRegisterRequest(email, password))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/auth/register")
            .post(body)
            .build()
        return execute(request)
    }

    suspend fun login(email: String, password: String): UserAuthResponse {
        val body = json.encodeToString(UserLoginRequest(email, password))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/auth/login")
            .post(body)
            .build()
        return execute(request)
    }

    suspend fun loginWithGoogle(idToken: String): UserAuthResponse {
        val body = json.encodeToString(GoogleAuthRequest(idToken))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/auth/google")
            .post(body)
            .build()
        return execute(request)
    }

    suspend fun verifyEmail(email: String, code: String): UserAuthResponse {
        val body = json.encodeToString(VerifyEmailRequest(email, code))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/auth/verify-email")
            .post(body)
            .build()
        return execute(request)
    }

    suspend fun resendVerification(email: String): ResendVerificationResponse {
        val body = json.encodeToString(ResendVerificationRequest(email))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/auth/resend-verification")
            .post(body)
            .build()
        return execute(request)
    }

    suspend fun forgotPassword(email: String): ForgotPasswordResponse {
        val body = json.encodeToString(ForgotPasswordRequest(email))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/auth/forgot-password")
            .post(body)
            .build()
        return execute(request)
    }

    suspend fun resetPassword(
        email: String,
        code: String,
        newPassword: String,
    ): ResetPasswordResponse {
        val body = json.encodeToString(ResetPasswordRequest(email, code, newPassword))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/auth/reset-password")
            .post(body)
            .build()
        return execute(request)
    }

    // Agent management endpoints require a user dashboard token in the Authorization header.

    suspend fun listAgents(userToken: String): List<AgentSummaryResponse> {
        val request = Request.Builder()
            .url("${baseUrl()}/api/agents")
            .get()
            .header("Authorization", "Bearer $userToken")
            .build()
        return execute(request)
    }

    suspend fun createAgent(userToken: String, name: String): CreateAgentResponse {
        val body = json.encodeToString(CreateAgentRequest(name))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/agents")
            .post(body)
            .header("Authorization", "Bearer $userToken")
            .build()
        return execute(request)
    }

    suspend fun reregisterAgent(userToken: String, agentId: String): ReRegisterAgentResponse {
        val request = Request.Builder()
            .url("${baseUrl()}/api/agents/$agentId/reregister")
            .post("".toRequestBody(jsonMediaType))
            .header("Authorization", "Bearer $userToken")
            .build()
        return execute(request)
    }

    suspend fun deleteAgent(userToken: String, agentId: String) {
        val request = Request.Builder()
            .url("${baseUrl()}/api/agents/$agentId")
            .delete()
            .header("Authorization", "Bearer $userToken")
            .build()
        executeNoContent(request)
    }

    /**
     * Schedule the calling user's account for soft-delete (14-day grace by
     * default). Server returns 204; subsequent WS-4010 close drives the
     * `SessionManager.AccountRemoved` cleanup automatically.
     */
    suspend fun requestAccountDeletion(
        userToken: String,
        body: DeleteAccountRequest,
    ) {
        val payload = json.encodeToString(body).toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/auth/me/delete")
            .post(payload)
            .header("Authorization", "Bearer $userToken")
            .build()
        executeNoContent(request)
    }

    suspend fun cancelAccountDeletion(userToken: String): CancelAccountDeletionResponse {
        val request = Request.Builder()
            .url("${baseUrl()}/api/auth/me/delete/cancel")
            .post("".toRequestBody(jsonMediaType))
            .header("Authorization", "Bearer $userToken")
            .build()
        return execute(request)
    }

    suspend fun getMobileTokens(
        userToken: String,
        agentId: String,
        body: MobileTokenRequest,
    ): MobileTokenResponse {
        val payload = json.encodeToString(body).toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/agents/$agentId/token")
            .post(payload)
            .header("Authorization", "Bearer $userToken")
            .build()
        return execute(request)
    }

    suspend fun listDevices(userToken: String): List<DeviceResponse> {
        val request = Request.Builder()
            .url("${baseUrl()}/api/devices")
            .get()
            .header("Authorization", "Bearer $userToken")
            .build()
        return execute(request)
    }

    suspend fun revokeDevice(userToken: String, deviceId: String) {
        val request = Request.Builder()
            .url("${baseUrl()}/api/devices/$deviceId")
            .delete()
            .header("Authorization", "Bearer $userToken")
            .build()
        executeNoContent(request)
    }

    /**
     * Claim a scanned QR pairing nonce. The server creates or transfers the
     * `agents` row, mints a fresh token, pushes the credentials to the agent
     * over its pairing WebSocket, and returns only the public-facing agent
     * id + friendly name. The token never reaches the mobile UI.
     *
     * Throws [ApiException] with HTTP 410 if the nonce is consumed/expired.
     */
    suspend fun claimAgentByPairing(
        userToken: String,
        body: PairingClaimRequest,
    ): PairingClaimResponse {
        val payload = json.encodeToString(body).toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/pairing/claim")
            .post(payload)
            .header("Authorization", "Bearer $userToken")
            .build()
        return execute(request)
    }

    // Prompt-template endpoints (per-user CRUD, JWT-authed via the user dashboard token).

    suspend fun listTemplates(userToken: String): List<TemplateDto> {
        val request = Request.Builder()
            .url("${baseUrl()}/api/templates")
            .get()
            .header("Authorization", "Bearer $userToken")
            .build()
        return execute(request)
    }

    suspend fun createTemplate(
        userToken: String,
        name: String,
        body: String,
    ): TemplateDto {
        val payload = json.encodeToString(CreateTemplateRequest(name, body))
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/templates")
            .post(payload)
            .header("Authorization", "Bearer $userToken")
            .build()
        return execute(request)
    }

    suspend fun updateTemplate(
        userToken: String,
        templateId: String,
        update: UpdateTemplateRequest,
    ): TemplateDto {
        val payload = json.encodeToString(update).toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("${baseUrl()}/api/templates/$templateId")
            .method("PATCH", payload)
            .header("Authorization", "Bearer $userToken")
            .build()
        return execute(request)
    }

    suspend fun deleteTemplate(userToken: String, templateId: String) {
        val request = Request.Builder()
            .url("${baseUrl()}/api/templates/$templateId")
            .delete()
            .header("Authorization", "Bearer $userToken")
            .build()
        executeNoContent(request)
    }

    suspend fun touchTemplate(userToken: String, templateId: String): TemplateDto {
        val request = Request.Builder()
            .url("${baseUrl()}/api/templates/$templateId/touch")
            .post("".toRequestBody(jsonMediaType))
            .header("Authorization", "Bearer $userToken")
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

    private suspend fun executeNoContent(request: Request) =
        withContext(Dispatchers.IO) {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    val errorBody = response.body?.string() ?: ""
                    throw ApiException(response.code, errorBody)
                }
            }
        }
}
