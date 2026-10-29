package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.ConnectionStore
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.DeleteAccountReauthErrorBody
import com.mobaide.app.data.network.dto.DeleteAccountRequest
import com.mobaide.app.data.network.dto.ResendRateLimitedErrorBody
import com.mobaide.app.data.network.dto.ResetErrorBody
import com.mobaide.app.data.network.dto.ResetIpRateLimitedErrorBody
import com.mobaide.app.data.network.dto.UserAuthResponse
import com.mobaide.app.data.network.dto.VerificationRequiredErrorBody
import kotlinx.serialization.json.Json

/**
 * Outcome of register / login / verify-email.
 */
sealed interface AuthResult {
    /**
     * Tokens have been persisted and the session is active.
     *
     * [scheduledDeletionAt] is non-null when the account is in the
     * soft-delete grace window — the UI surfaces a "cancel scheduled
     * deletion" banner.
     */
    data class Success(
        val email: String,
        val scheduledDeletionAt: String? = null,
    ) : AuthResult

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

/**
 * Outcome of a forgot-password request. The server always returns 200 to
 * defeat enumeration, so [Sent] is the happy path. [RateLimited] surfaces
 * the per-IP cap (HTTP 429) so the UI can show a cooldown.
 */
sealed interface PasswordResetOutcome {
    data object Sent : PasswordResetOutcome
    data class RateLimited(val retryAfterSeconds: Int) : PasswordResetOutcome
}

/**
 * Discriminated failure type for the /reset-password endpoint. Decoded from
 * the FastAPI `{"detail":{"error": ...}}` body so the ViewModel can show
 * a tailored message without parsing strings.
 */
sealed interface ResetPasswordError {
    data object InvalidCode : ResetPasswordError
    data object ExpiredCode : ResetPasswordError
    data object AttemptsExhausted : ResetPasswordError
    data object SameAsOld : ResetPasswordError
    data class IpRateLimited(val retryAfterSeconds: Int) : ResetPasswordError
    data class Unknown(val code: Int, val message: String) : ResetPasswordError
}

class ResetPasswordFailure(val reason: ResetPasswordError) : Exception(
    "Password reset failed: $reason"
)

/**
 * How the server should process a self-delete request.
 *
 * [Scheduled] (default) writes a 14-day soft-delete; the user can cancel
 * within the grace window by signing back in or clicking the email link.
 * [Immediate] cascades the purge now — no grace, no recovery.
 */
enum class DeletionMode(val wire: String) {
    Scheduled("scheduled"),
    Immediate("immediate"),
}

/**
 * Outcome of a request to permanently delete the calling user's account.
 * The happy path is [Submitted] — for [DeletionMode.Scheduled] the server has
 * queued the 14-day soft-delete; for [DeletionMode.Immediate] the server has
 * already cascade-purged. In both cases the WebSocket 4010 close drives
 * [SessionManager.AccountRemoved] cleanup automatically; no extra plumbing
 * is required here.
 */
sealed interface DeleteAccountResult {
    data class Submitted(val mode: DeletionMode) : DeleteAccountResult
    data class ReauthFailed(val message: String) : DeleteAccountResult
    data object Forbidden : DeleteAccountResult
}

/**
 * Outcome of a request to cancel a pending account deletion (restore the
 * account from the grace window).
 */
sealed interface CancelDeletionResult {
    data object Cancelled : CancelDeletionResult
    data object NotScheduled : CancelDeletionResult
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

    /**
     * The password typed on the way to the code screen, kept in memory only
     * so [verifyEmail] can prove it: if someone re-registered the address in
     * the meantime, the server installs this one instead of theirs.
     */
    @Volatile
    private var pendingVerification: PendingVerification? = null

    private data class PendingVerification(val email: String, val password: String) {
        override fun toString() = "PendingVerification(email=$email)"
    }

    suspend fun register(email: String, password: String): AuthResult {
        val response = centralApi.register(email, password)
        return finaliseOrFlagVerification(email, response)
            .alsoRememberPending(password)
    }

    suspend fun loginWithPassword(email: String, password: String): AuthResult {
        val response = try {
            centralApi.login(email, password)
        } catch (e: ApiException) {
            parseVerificationRequired(e)?.let { pending ->
                return AuthResult.VerificationRequired(pending).alsoRememberPending(password)
            }
            throw e
        }
        return finaliseOrFlagVerification(email, response)
            .alsoRememberPending(password)
    }

    suspend fun loginWithGoogle(idToken: String): AuthResult {
        val response = centralApi.loginWithGoogle(idToken)
        // Google OAuth always returns a verified user server-side, so any
        // VerificationRequired here would be unexpected — surface generically.
        return finaliseOrFlagVerification(response.email, response)
    }

    suspend fun verifyEmail(email: String, code: String): AuthResult.Success {
        val password = pendingVerification
            ?.takeIf { it.email.equals(email.trim(), ignoreCase = true) }
            ?.password
        val response = centralApi.verifyEmail(email, code, password)
        pendingVerification = null
        if (response.role == ROLE_ADMIN) throw AdminLoginNotSupportedException()
        val token = response.access_token
            ?: error("verify-email response missing access_token")
        userIdentityStore.saveUserSession(
            token, response.email, response.user_id, response.role,
            scheduledDeletionAt = response.scheduled_deletion_at,
        )
        sessionManager.markUserLoggedIn()
        return AuthResult.Success(response.email, response.scheduled_deletion_at)
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

    suspend fun requestPasswordReset(email: String): PasswordResetOutcome {
        return try {
            centralApi.forgotPassword(email)
            PasswordResetOutcome.Sent
        } catch (e: ApiException) {
            if (e.code == 429) {
                val wait = parseResetIpRetryAfter(e) ?: 60
                return PasswordResetOutcome.RateLimited(wait)
            }
            throw e
        }
    }

    /**
     * Consume a reset OTP and set a new password. Throws [ResetPasswordFailure]
     * with a discriminated [ResetPasswordError] reason on any handled error;
     * other ApiExceptions propagate.
     */
    suspend fun resetPassword(email: String, code: String, newPassword: String) {
        try {
            centralApi.resetPassword(email, code, newPassword)
        } catch (e: ApiException) {
            throw ResetPasswordFailure(mapResetError(e))
        }
    }

    private fun mapResetError(e: ApiException): ResetPasswordError {
        val errorTag = try {
            errorJson.decodeFromString<ResetErrorBody>(e.errorBody).detail.error
        } catch (_: Exception) {
            null
        }
        return when {
            e.code == 410 -> ResetPasswordError.ExpiredCode
            e.code == 429 && errorTag == "reset_ip_rate_limited" -> {
                val wait = parseResetIpRetryAfter(e) ?: 60
                ResetPasswordError.IpRateLimited(wait)
            }
            e.code == 429 -> ResetPasswordError.AttemptsExhausted
            e.code == 400 && errorTag == "new_password_same_as_old" ->
                ResetPasswordError.SameAsOld
            e.code == 400 -> ResetPasswordError.InvalidCode
            else -> ResetPasswordError.Unknown(e.code, e.message ?: "")
        }
    }

    private fun parseResetIpRetryAfter(e: ApiException): Int? {
        return try {
            errorJson
                .decodeFromString<ResetIpRateLimitedErrorBody>(e.errorBody)
                .detail.retry_after_seconds
                .takeIf { it > 0 }
        } catch (_: Exception) {
            null
        }
    }

    private fun finaliseOrFlagVerification(
        requestedEmail: String,
        response: UserAuthResponse,
    ): AuthResult {
        if (response.role == ROLE_ADMIN) throw AdminLoginNotSupportedException()
        if (response.status == STATUS_VERIFICATION_REQUIRED || response.access_token == null) {
            return AuthResult.VerificationRequired(response.email.ifBlank { requestedEmail })
        }
        userIdentityStore.saveUserSession(
            response.access_token, response.email, response.user_id, response.role,
            scheduledDeletionAt = response.scheduled_deletion_at,
        )
        sessionManager.markUserLoggedIn()
        return AuthResult.Success(response.email, response.scheduled_deletion_at)
    }

    private fun AuthResult.alsoRememberPending(password: String): AuthResult {
        pendingVerification = (this as? AuthResult.VerificationRequired)
            ?.let { PendingVerification(it.email.trim(), password) }
        return this
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

    /**
     * Request account deletion using a current password as re-auth proof.
     * [mode] picks scheduled (14-day grace) vs immediate (cascade now). On
     * success the server returns 204; the WS-4010 close that follows drives
     * [SessionManager] `AccountRemoved` cleanup automatically.
     */
    suspend fun deleteAccountWithPassword(
        password: String,
        mode: DeletionMode,
    ): DeleteAccountResult {
        val token = userIdentityStore.getUserAccessToken()
            ?: return DeleteAccountResult.ReauthFailed("Not signed in")
        return submitDelete(
            token,
            mode,
            DeleteAccountRequest(
                method = "password",
                mode = mode.wire,
                password = password,
            ),
        )
    }

    /**
     * Request account deletion using a fresh Google ID token + the nonce
     * the client used when obtaining it (Auth0-style re-auth — the server
     * validates iat freshness, nonce match, and that the token's sub
     * matches the user's stored google_sub). [mode] picks scheduled vs
     * immediate cascade purge.
     */
    suspend fun deleteAccountWithGoogle(
        idToken: String,
        nonce: String,
        mode: DeletionMode,
    ): DeleteAccountResult {
        val token = userIdentityStore.getUserAccessToken()
            ?: return DeleteAccountResult.ReauthFailed("Not signed in")
        return submitDelete(
            token,
            mode,
            DeleteAccountRequest(
                method = "google",
                mode = mode.wire,
                id_token = idToken,
                nonce = nonce,
            ),
        )
    }

    private suspend fun submitDelete(
        userToken: String,
        mode: DeletionMode,
        body: DeleteAccountRequest,
    ): DeleteAccountResult {
        return try {
            centralApi.requestAccountDeletion(userToken, body)
            DeleteAccountResult.Submitted(mode)
        } catch (e: ApiException) {
            when (e.code) {
                400 -> DeleteAccountResult.Forbidden
                403 -> DeleteAccountResult.ReauthFailed(parseDeleteReauthMessage(e))
                else -> throw e
            }
        }
    }

    private fun parseDeleteReauthMessage(e: ApiException): String {
        return try {
            errorJson
                .decodeFromString<DeleteAccountReauthErrorBody>(e.errorBody)
                .detail.message
        } catch (_: Exception) {
            "Re-authentication failed"
        }
    }

    /**
     * Cancel a pending scheduled deletion. Idempotent. Returns
     * [CancelDeletionResult.NotScheduled] if the server reports the account
     * had no pending deletion (helpful for de-duplicating the banner when the
     * user already cancelled elsewhere). On success, the local scheduled-
     * deletion marker is cleared so the banner disappears immediately.
     */
    suspend fun cancelAccountDeletion(): CancelDeletionResult {
        val token = userIdentityStore.getUserAccessToken()
            ?: return CancelDeletionResult.NotScheduled
        val response = centralApi.cancelAccountDeletion(token)
        userIdentityStore.setScheduledDeletionAt(null)
        return if (response.scheduled_deletion_at == null) {
            CancelDeletionResult.NotScheduled
        } else {
            CancelDeletionResult.Cancelled
        }
    }

    /**
     * ISO-8601 timestamp the server reports as the scheduled-deletion final
     * purge date when the account is in the soft-delete grace window, or
     * `null` if the account is active.
     */
    fun getScheduledDeletionAt(): String? = userIdentityStore.getScheduledDeletionAt()
}
