package com.claudemobile.app.data.network.dto

import kotlinx.serialization.Serializable

/** Agent listing response — no credentials. Matches server `AgentSummary`. */
@Serializable
data class AgentSummaryResponse(
    val agent_id: String,
    val name: String,
    val online: Boolean,
    val created_at: String,
    val last_seen_at: String? = null,
)

@Serializable
data class CreateAgentRequest(val name: String)

/** Returned once on creation — includes raw token. */
@Serializable
data class CreateAgentResponse(
    val agent_id: String,
    val name: String,
    val token: String,
)

/** Returned on credential rotation — includes new raw token. */
@Serializable
data class ReRegisterAgentResponse(
    val agent_id: String,
    val token: String,
)

/** Mobile access + refresh tokens for connecting to an agent via relay. */
@Serializable
data class MobileTokenResponse(
    val access_token: String,
    val refresh_token: String,
    val token_type: String = "bearer",
)
