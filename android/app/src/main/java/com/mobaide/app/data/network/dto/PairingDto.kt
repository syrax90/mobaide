package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/** JSON payload encoded in the pairing QR code. */
@Serializable
data class QrPayload(
    val type: String,
    val token: String,
    val server: String,
    val agent_id: String,
)

/** Request body for `POST /api/pairing/complete`. */
@Serializable
data class PairingCompleteRequest(
    val token: String,
    val device_id: String,
    val device_name: String? = null,
)

/** Response from `POST /api/pairing/complete`. */
@Serializable
data class PairingCompleteResponse(
    val access_token: String,
    val refresh_token: String,
    val agent_id: String,
    val pairing_id: String,
    val token_type: String = "bearer",
)
