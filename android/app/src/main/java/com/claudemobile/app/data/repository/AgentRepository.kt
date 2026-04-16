package com.claudemobile.app.data.repository

import com.claudemobile.app.data.auth.UserIdentityStore
import com.claudemobile.app.data.network.CentralApi
import com.claudemobile.app.data.network.dto.AgentSummaryResponse
import com.claudemobile.app.data.network.dto.CreateAgentResponse
import com.claudemobile.app.data.network.dto.MobileTokenResponse
import com.claudemobile.app.data.network.dto.ReRegisterAgentResponse
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Repository for user-owned agent management via the central server.
 * Caches the agent list in-memory and exposes it as a [StateFlow].
 */
class AgentRepository(
    private val centralApi: CentralApi,
    private val userIdentityStore: UserIdentityStore,
) {
    private val _agents = MutableStateFlow<List<AgentSummaryResponse>>(emptyList())

    fun observeAgents(): StateFlow<List<AgentSummaryResponse>> = _agents.asStateFlow()

    suspend fun refreshAgents() {
        val token = requireUserToken()
        _agents.value = centralApi.listAgents(token)
    }

    suspend fun createAgent(name: String): CreateAgentResponse {
        val token = requireUserToken()
        val result = centralApi.createAgent(token, name)
        refreshAgents()
        return result
    }

    suspend fun reregisterAgent(agentId: String): ReRegisterAgentResponse {
        val token = requireUserToken()
        return centralApi.reregisterAgent(token, agentId)
    }

    suspend fun deleteAgent(agentId: String) {
        val token = requireUserToken()
        centralApi.deleteAgent(token, agentId)
        refreshAgents()
    }

    suspend fun getMobileTokensForAgent(agentId: String): MobileTokenResponse {
        val token = requireUserToken()
        return centralApi.getMobileTokens(token, agentId)
    }

    private fun requireUserToken(): String =
        userIdentityStore.getUserAccessToken()
            ?: throw IllegalStateException("User not logged in")
}
