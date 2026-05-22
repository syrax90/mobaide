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
    val mobile_device_name: String? = null,
    val hostname_hint: String? = null,
    val friendly_name: String? = null,
)

/**
 * Server response on a successful claim — never includes the token.
 *
 * [ws_delivered] is an optional, forward-compatible hint about whether the
 * credential push was dispatched to the agent's pairing WebSocket at claim
 * time (`true`) or staged in the server-side recovery slot for the agent's
 * next reconnect (`false`). `null` from older central-server builds that
 * predate the field. The mobile uses this only to refine the timeout-error
 * copy in [QrPairingState.WaitingForOnline]; the authoritative
 * "actually paired" signal remains the routing-table `online=true` event.
 */
@Serializable
data class PairingClaimResponse(
    val agent_id: String,
    val agent_friendly_name: String,
    val ws_delivered: Boolean? = null,
)
