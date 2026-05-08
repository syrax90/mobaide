package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.CreateAgentResponse
import com.mobaide.app.data.network.dto.MobileTokenResponse
import com.mobaide.app.data.network.dto.PairingClaimRequest
import com.mobaide.app.data.network.dto.PairingClaimResponse
import com.mobaide.app.data.network.dto.ReRegisterAgentResponse
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

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

    /**
     * Patch the online flag on a single agent in the cached list.
     * Idempotent; a no-op if the agent isn't in the cache (e.g. it was
     * just created on another device and we haven't refreshed yet — the
     * next `refreshAgents` call will pick it up).
     */
    fun updateAgentOnline(agentId: String, online: Boolean) {
        _agents.update { current ->
            var changed = false
            val next = current.map { a ->
                if (a.agent_id == agentId && a.online != online) {
                    changed = true
                    a.copy(online = online)
                } else a
            }
            if (changed) next else current
        }
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

    /**
     * Claim a scanned QR pairing payload. The server creates or
     * transfers the agent row and pushes the credentials directly to the
     * agent over its pairing WebSocket. Refreshes the agent list on
     * success; the new agent appears within one round-trip.
     */
    suspend fun claimAgentByPairing(
        nonce: String,
        trustKeyFp: String,
        mobileDeviceId: String,
        hostnameHint: String?,
        friendlyName: String?,
    ): PairingClaimResponse {
        val token = requireUserToken()
        val response = centralApi.claimAgentByPairing(
            token,
            PairingClaimRequest(
                nonce = nonce,
                trust_key_fp = trustKeyFp,
                mobile_device_id = mobileDeviceId,
                hostname_hint = hostnameHint,
                friendly_name = friendlyName,
            ),
        )
        refreshAgents()
        return response
    }

    private fun requireUserToken(): String =
        userIdentityStore.getUserAccessToken()
            ?: throw IllegalStateException("User not logged in")
}
