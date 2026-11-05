package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

@Serializable
data class TokenRequest(
    val username: String,
    val password: String,
)

@Serializable
data class TokenResponse(
    val access_token: String,
    val refresh_token: String,
    val token_type: String = "bearer",
)

@Serializable
data class RefreshRequest(
    val refresh_token: String,
)

@Serializable
data class UserRegisterRequest(
    val email: String,
    val password: String,
    /** Per-install device id; binds the session so revoking the device signs it out. */
    val device_id: String? = null,
)

@Serializable
data class UserLoginRequest(
    val email: String,
    val password: String,
    /** Per-install device id; binds the session so revoking the device signs it out. */
    val device_id: String? = null,
)

@Serializable
data class GoogleAuthRequest(
    val id_token: String,
    /** Per-install device id; binds the session so revoking the device signs it out. */
    val device_id: String? = null,
)

@Serializable
data class UserAuthResponse(
    val access_token: String? = null,
    val token_type: String = "bearer",
    val user_id: String,
    val email: String,
    val role: String,
    /**
     * `"ok"` for verified accounts (access_token populated);
     * `"verification_required"` when the client must call /verify-email
     * with the OTP before receiving a JWT.
     */
    val status: String = "ok",
    /**
     * ISO-8601 timestamp when the account is currently scheduled for
     * permanent deletion, or `null` if no deletion is pending. When set,
     * clients render a "cancel scheduled deletion" banner.
     */
    val scheduled_deletion_at: String? = null,
)

@Serializable
data class VerifyEmailRequest(
    val email: String,
    val code: String,
    /** The password typed to reach the code screen; see AuthRepository.verifyEmail. */
    val password: String? = null,
    /** Per-install device id; binds the session so revoking the device signs it out. */
    val device_id: String? = null,
)

@Serializable
data class ResendVerificationRequest(
    val email: String,
)

@Serializable
data class ResendVerificationResponse(
    val status: String,
    val cooldown_seconds_remaining: Int = 0,
)

/**
 * Represent the 403 error body from `/api/auth/login` when the account is not yet verified.
 * FastAPI wraps it as `{"detail": {"error": ..., "email": ...}}`.
 */
@Serializable
data class VerificationRequiredDetail(
    val error: String,
    val email: String? = null,
)

@Serializable
data class VerificationRequiredErrorBody(
    val detail: VerificationRequiredDetail,
)

/**
 * Represent the 429 error body from `/api/auth/resend-verification`.
 */
@Serializable
data class ResendRateLimitedDetail(
    val error: String,
    val cooldown_seconds_remaining: Int = 0,
)

@Serializable
data class ResendRateLimitedErrorBody(
    val detail: ResendRateLimitedDetail,
)

@Serializable
data class ForgotPasswordRequest(val email: String)

@Serializable
data class ForgotPasswordResponse(val status: String = "ok")

@Serializable
data class ResetPasswordRequest(
    val email: String,
    val code: String,
    val new_password: String,
)

@Serializable
data class ResetPasswordResponse(val status: String = "ok")

/**
 * Represents the 429 error body from `/api/auth/forgot-password` or
 * `/api/auth/reset-password` when the per-IP cap is exhausted, and from
 * `/api/auth/login` after too many wrong passwords.
 * Wrapped by FastAPI as `{"detail": {"error": ..., "retry_after_seconds": ...}}`.
 */
@Serializable
data class ResetIpRateLimitedDetail(
    val error: String,
    val retry_after_seconds: Int = 0,
)

@Serializable
data class ResetIpRateLimitedErrorBody(
    val detail: ResetIpRateLimitedDetail,
)

/**
 * Represents the 400 error body for /reset-password when the new password
 * matches the current one, or the code is invalid. FastAPI wraps as
 * `{"detail": {"error": ...}}` — the `error` discriminator drives ViewModel
 * error mapping.
 */
@Serializable
data class ResetErrorDetail(val error: String)

@Serializable
data class ResetErrorBody(val detail: ResetErrorDetail)

/**
 * Re-auth proof for `POST /api/auth/me/delete`. Exactly one of password
 * (for password users) or (id_token + nonce) (for Google users) must be
 * populated; `method` discriminates server-side. `mode` chooses scheduled
 * (14-day grace, default when omitted) vs immediate cascade purge.
 */
@Serializable
data class DeleteAccountRequest(
    val method: String,
    val mode: String? = null,
    val password: String? = null,
    val id_token: String? = null,
    val nonce: String? = null,
)

@Serializable
data class CancelAccountDeletionResponse(
    val status: String = "cancelled",
    val scheduled_deletion_at: String? = null,
)

/**
 * `GET /api/auth/me`. `first_paired_at` is the server's never-cleared
 * first-pairing stamp (ISO-8601, UTC), `null` until the account pairs its
 * first agent.
 */
@Serializable
data class MeResponse(
    val user_id: String,
    val email: String,
    val role: String,
    val created_at: String,
    val scheduled_deletion_at: String? = null,
    val first_paired_at: String? = null,
)

/**
 * 403 error body from `/api/auth/me/delete` when the supplied credential
 * proof is rejected. FastAPI wraps as `{"detail": {"error": "reauth_required", "message": ...}}`.
 */
@Serializable
data class DeleteAccountReauthDetail(
    val error: String,
    val message: String,
)

@Serializable
data class DeleteAccountReauthErrorBody(
    val detail: DeleteAccountReauthDetail,
)
