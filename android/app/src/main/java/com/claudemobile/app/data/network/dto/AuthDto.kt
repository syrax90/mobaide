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

// -- Central server user auth DTOs -------------------------------------------

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
    val access_token: String,
    val token_type: String = "bearer",
    val user_id: String,
    val email: String,
    val role: String,
)
