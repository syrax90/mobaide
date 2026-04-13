package com.claudemobile.app.data.repository

import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.network.RestApi

class AuthRepository(
    private val restApi: RestApi,
    private val tokenStore: TokenStore,
    private val sessionManager: SessionManager,
) {
    suspend fun login(agentUrl: String, username: String, password: String) {
        val response = restApi.login(agentUrl, username, password)
        // Persist first, then flip the observable state. Ordering matters
        // so that any observer that races on LoggedIn can read the tokens
        // it needs without seeing a half-initialised session.
        tokenStore.saveAgentUrl(agentUrl)
        tokenStore.saveTokens(response.access_token, response.refresh_token)
        sessionManager.markLoggedIn()
    }

    /**
     * Tear the session down. Routes through [SessionManager.forceLogout]
     * so that token clearing and the state-flip happen in one critical
     * section — otherwise a concurrent observer could see "LoggedIn with
     * no tokens", which is exactly the state that used to crash the
     * WebSocket URL provider.
     */
    fun logout() {
        sessionManager.forceLogout()
    }

    fun isLoggedIn(): Boolean = sessionManager.isLoggedIn()

    fun getAgentUrl(): String? = tokenStore.getAgentUrl()

    fun getUsername(): String? = tokenStore.getUsername()
}
