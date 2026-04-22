package com.claudemobile.app.data.network.dto

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
)

@Serializable
data class UserLoginRequest(
    val email: String,
    val password: String,
)

@Serializable
data class GoogleAuthRequest(
    val id_token: String,
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
)

@Serializable
data class VerifyEmailRequest(
    val email: String,
    val code: String,
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
