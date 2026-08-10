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
    val platform: String? = null,
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

/**
 * Server response to `POST /api/pairing/device-code`.
 *
 * [request_id] is the handle every follow-up call uses; the code itself never
 * travels in a URL, where the relay's access log would capture it.
 */
@Serializable
data class DeviceCodeMintResponse(
    val request_id: String,
    val code: String,
    val expires_at: String,
    val verification_command: String,
)

/**
 * Poll state for `GET /api/pairing/device-code/{request_id}`.
 *
 * [hostname] and [os] are reported by whichever machine entered the code, over
 * an endpoint that authenticates nobody — they let a user recognise their own
 * host and prove nothing on their own. The control that stops a stranger's
 * machine joining this account is the confirmation the agent prints on its own
 * terminal, naming this account before it writes credentials.
 *
 * [blocked_reason] is `unknown_agent` or `owned_by_other` when confirming is
 * already destined to fail, so the UI can say so before the tap rather than
 * burning the code on it.
 *
 * [repair_agent_name] is set only when the redeeming host claims an agent this
 * account already owns. Approving then *moves* that agent — rotating its token
 * onto the redeemer and knocking the real host offline — so the confirm screen
 * must name it instead of asking the first-pair question.
 */
@Serializable
data class DeviceCodeStatusResponse(
    val status: String,
    val expires_at: String,
    val hostname: String? = null,
    val os: String? = null,
    val is_repair: Boolean = false,
    val repair_agent_name: String? = null,
    val blocked_reason: String? = null,
)

/**
 * Approval body for `POST /api/pairing/device-code/{request_id}/confirm`.
 *
 * Carries the same device fields as [PairingClaimRequest] because the relay
 * runs the identical completion path.
 */
@Serializable
data class DeviceCodeConfirmRequest(
    val mobile_device_id: String,
    val mobile_device_name: String? = null,
    val platform: String? = null,
    val friendly_name: String? = null,
)

/**
 * Response to `GET /api/pairing/trust-pin` — display and comparison only.
 *
 * [account_trust_key_fp] must NOT be written into [TrustKeyPinning].
 */
@Serializable
data class TrustPinResponse(
    val relay_url: String,
    val relay_trust_key_fp: String,
    val account_trust_key_fp: String? = null,
)
