package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.ConnectionStore
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.ResendRateLimitedErrorBody
import com.mobaide.app.data.network.dto.UserAuthResponse
import com.mobaide.app.data.network.dto.VerificationRequiredErrorBody
import kotlinx.serialization.json.Json

/**
 * Outcome of register / login / verify-email.
 */
sealed interface AuthResult {
    /** Tokens have been persisted and the session is active. */
    data class Success(val email: String) : AuthResult

    /**
     * The server accepted the call but the account is not yet verified.
     * The caller must route the user to the verify-email screen.
     */
    data class VerificationRequired(val email: String) : AuthResult
}

/**
 * Outcome of a resend-verification call.
 */
sealed interface ResendOutcome {
    object Sent : ResendOutcome
    data class RateLimited(val cooldownSecondsRemaining: Int) : ResendOutcome
}

class AuthRepository(
    private val restApi: RestApi,
    private val centralApi: CentralApi,
    private val tokenStore: TokenStore,
    private val userIdentityStore: UserIdentityStore,
    private val sessionManager: SessionManager,
    private val connectionStore: ConnectionStore,
) {

    private val errorJson = Json { ignoreUnknownKeys = true }

    suspend fun register(email: String, password: String): AuthResult {
        val response = centralApi.register(email, password)
        return finaliseOrFlagVerification(email, response)
    }

    suspend fun loginWithPassword(email: String, password: String): AuthResult {
        val response = try {
            centralApi.login(email, password)
        } catch (e: ApiException) {
            parseVerificationRequired(e)?.let { pending ->
                return AuthResult.VerificationRequired(pending)
            }
            throw e
        }
        return finaliseOrFlagVerification(email, response)
    }

    suspend fun loginWithGoogle(idToken: String): AuthResult {
        val response = centralApi.loginWithGoogle(idToken)
        // Google OAuth always returns a verified user server-side, so any
        // VerificationRequired here would be unexpected — surface generically.
        return finaliseOrFlagVerification(response.email, response)
    }

    suspend fun verifyEmail(email: String, code: String): AuthResult.Success {
        val response = centralApi.verifyEmail(email, code)
        if (response.role == ROLE_ADMIN) throw AdminLoginNotSupportedException()
        val token = response.access_token
            ?: error("verify-email response missing access_token")
        userIdentityStore.saveUserSession(
            token, response.email, response.user_id, response.role,
        )
        sessionManager.markUserLoggedIn()
        return AuthResult.Success(response.email)
    }

    suspend fun resendVerification(email: String): ResendOutcome {
        return try {
            centralApi.resendVerification(email)
            ResendOutcome.Sent
        } catch (e: ApiException) {
            if (e.code == 429) {
                val cooldown = parseRateLimitCooldown(e) ?: 60
                return ResendOutcome.RateLimited(cooldown)
            }
            throw e
        }
    }

    private fun finaliseOrFlagVerification(
        requestedEmail: String,
        response: UserAuthResponse,
    ): AuthResult {
        if (response.role == ROLE_ADMIN) throw AdminLoginNotSupportedException()
        if (response.status == STATUS_VERIFICATION_REQUIRED || response.access_token == null) {
            // Do NOT persist tokens or flip the session state.
            return AuthResult.VerificationRequired(response.email.ifBlank { requestedEmail })
        }
        userIdentityStore.saveUserSession(
            response.access_token, response.email, response.user_id, response.role,
        )
        sessionManager.markUserLoggedIn()
        return AuthResult.Success(response.email)
    }

    private fun parseVerificationRequired(e: ApiException): String? {
        if (e.code != 403) return null
        return try {
            val body = errorJson.decodeFromString<VerificationRequiredErrorBody>(e.errorBody)
            body.detail.takeIf { it.error == VERIFICATION_REQUIRED_ERROR }?.email
        } catch (_: Exception) {
            null
        }
    }

    private fun parseRateLimitCooldown(e: ApiException): Int? {
        return try {
            val body = errorJson.decodeFromString<ResendRateLimitedErrorBody>(e.errorBody)
            body.detail.cooldown_seconds_remaining.takeIf { it > 0 }
        } catch (_: Exception) {
            null
        }
    }

    private companion object {
        const val ROLE_ADMIN = "admin"
        const val STATUS_VERIFICATION_REQUIRED = "verification_required"
        const val VERIFICATION_REQUIRED_ERROR = "verification_required"
    }

    fun isUserAuthenticated(): Boolean = sessionManager.isUserAuthenticated()

    fun getUserEmail(): String? = userIdentityStore.getUserEmail()

    /**
     * Full logout: clears user identity AND agent-connection tokens.
     */
    fun userLogout() {
        sessionManager.userLogout()
    }

    suspend fun connectAgent(agentUrl: String, username: String, password: String) {
        val response = restApi.login(agentUrl, username, password)
        // Clear any relay-mode keys from a previous session so that
        // getConnectionMode() returns DIRECT after a direct login.
        tokenStore.clear()
        // Persist first, then flip the observable state. Ordering matters
        // so that any observer that races on FullyConnected can read the
        // tokens it needs without seeing a half-initialised session.
        tokenStore.saveAgentUrl(agentUrl)
        tokenStore.saveTokens(response.access_token, response.refresh_token)
        sessionManager.markAgentConnected()

        connectionStore.captureActiveProfile(
            tokenStore,
            label = "Direct: $agentUrl",
        )
    }

    /**
     * Disconnect from the current agent but keep the user identity.
     * Transitions FullyConnected → UserOnly.
     */
    fun disconnectAgent() {
        sessionManager.agentDisconnect()
    }

    /**
     * Force full logout via [SessionManager.forceLogout] so token clearing
     * and the state-flip happen atomically.
     */
    fun logout() {
        sessionManager.forceLogout()
    }

    fun isLoggedIn(): Boolean = sessionManager.isLoggedIn()

    fun getAgentUrl(): String? = tokenStore.getAgentUrl()

    fun getUsername(): String? = tokenStore.getUsername()
}
