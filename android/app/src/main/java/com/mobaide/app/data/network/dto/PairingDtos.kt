package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * Mobile-side claim request body for `POST /api/pairing/claim`.
 *
 * The token is never carried in this flow — the server delivers the
 * minted credentials directly to the agent over its pairing WebSocket.
 * The mobile only sees `agent_id` and `agent_friendly_name` come back.
 */
@Serializable
data class PairingClaimRequest(
    val nonce: String,
    val trust_key_fp: String,
    val mobile_device_id: String,
    val hostname_hint: String? = null,
    val friendly_name: String? = null,
)

/** Server response on a successful claim — never includes the token. */
@Serializable
data class PairingClaimResponse(
    val agent_id: String,
    val agent_friendly_name: String,
)
