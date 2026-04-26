package com.claudemobile.app.data.repository

import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.dto.ForceUpdateResponse

/**
 * Surfaces the user-triggered "Install now" agent-update flow as a thin
 * repository over the agent's force-update HTTP endpoint. The agent
 * responds with the same outcome string its updater state machine
 * returns, so callers can map directly to a user-visible status.
 */
class AgentUpdateRepository(
    private val restApi: RestApi,
) {
    suspend fun forceUpdateNow(): Result<ForceUpdateResponse> = runCatching {
        restApi.forceAgentUpdate()
    }
}
