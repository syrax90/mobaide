package com.claudemobile.app.data.auth

import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AuthInterceptor @Inject constructor(
    private val tokenStore: TokenStore,
) : Interceptor {

    private val json = Json { ignoreUnknownKeys = true }

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val path = request.url.encodedPath

        // Skip auth for login and refresh endpoints
        if (path.endsWith("/api/auth/token") || path.endsWith("/api/auth/refresh")) {
            return chain.proceed(request)
        }

        // Refresh token if expiring soon
        if (tokenStore.isAccessTokenExpiringSoon()) {
            refreshTokenSync(chain)
        }

        val token = tokenStore.getAccessToken()
        val authenticatedRequest = if (token != null) {
            request.newBuilder()
                .header("Authorization", "Bearer $token")
                .build()
        } else {
            request
        }

        val response = chain.proceed(authenticatedRequest)

        // If 401, try refreshing once
        if (response.code == 401 && token != null) {
            response.close()
            val refreshed = refreshTokenSync(chain)
            if (refreshed) {
                val newToken = tokenStore.getAccessToken()
                val retryRequest = request.newBuilder()
                    .header("Authorization", "Bearer $newToken")
                    .build()
                return chain.proceed(retryRequest)
            }
            tokenStore.clear()
        }

        return response
    }

    private fun refreshTokenSync(chain: Interceptor.Chain): Boolean {
        val refreshToken = tokenStore.getRefreshToken() ?: return false
        val serverUrl = tokenStore.getServerUrl() ?: return false

        val body = """{"refresh_token":"$refreshToken"}"""
            .toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url("$serverUrl/api/auth/refresh")
            .post(body)
            .build()

        return try {
            val response = chain.proceed(request)
            if (response.isSuccessful) {
                val responseBody = response.body?.string() ?: return false
                val tokenResponse = json.decodeFromString<TokenResponseDto>(responseBody)
                tokenStore.saveTokens(tokenResponse.access_token, tokenResponse.refresh_token)
                true
            } else {
                false
            }
        } catch (_: Exception) {
            false
        }
    }

    @kotlinx.serialization.Serializable
    private data class TokenResponseDto(
        val access_token: String,
        val refresh_token: String,
    )
}
