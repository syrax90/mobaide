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
