package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/** Represent a listed agent without credentials. Matches server `AgentSummary`. */
@Serializable
data class AgentSummaryResponse(
    val agent_id: String,
    val name: String,
    val online: Boolean,
    val created_at: String,
    val last_seen_at: String? = null,
    /** The build that last connected can uninstall itself when removed. */
    val supports_uninstall: Boolean = false,
)

@Serializable
data class CreateAgentRequest(val name: String)

/** Carry the raw token returned once on agent creation. */
@Serializable
data class CreateAgentResponse(
    val agent_id: String,
    val name: String,
    val token: String,
)

/** Carry the new raw token issued after credential rotation. */
@Serializable
data class ReRegisterAgentResponse(
    val agent_id: String,
    val token: String,
)

/** Hold mobile access and refresh tokens for connecting to an agent via relay. */
@Serializable
data class MobileTokenResponse(
    val access_token: String,
    val refresh_token: String,
    val token_type: String = "bearer",
)

/**
 * Body for `POST /api/agents/{agent_id}/token`. Tells the server which
 * per-install device is requesting tokens so the issued JWT's `sub`
 * claim and the refresh-token row are scoped to this device, not the
 * user account.
 */
@Serializable
data class MobileTokenRequest(
    val mobile_device_id: String,
    val mobile_device_name: String? = null,
    val platform: String? = null,
)
