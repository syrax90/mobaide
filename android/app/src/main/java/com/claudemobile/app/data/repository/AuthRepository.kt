package com.claudemobile.app.data.repository

import com.claudemobile.app.data.auth.ConnectionStore
import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.auth.UserIdentityStore
import com.claudemobile.app.data.network.CentralApi
import com.claudemobile.app.data.network.RestApi

class AuthRepository(
    private val restApi: RestApi,
    private val centralApi: CentralApi,
    private val tokenStore: TokenStore,
    private val userIdentityStore: UserIdentityStore,
    private val sessionManager: SessionManager,
    private val connectionStore: ConnectionStore,
) {

    // -- User identity (central server) --------------------------------------

    suspend fun register(email: String, password: String) {
        val response = centralApi.register(email, password)
        if (response.role == ROLE_ADMIN) throw AdminLoginNotSupportedException()
        userIdentityStore.saveUserSession(
            response.access_token, response.email, response.user_id, response.role,
        )
        sessionManager.markUserLoggedIn()
    }

    suspend fun loginWithPassword(email: String, password: String) {
        val response = centralApi.login(email, password)
        if (response.role == ROLE_ADMIN) throw AdminLoginNotSupportedException()
        userIdentityStore.saveUserSession(
            response.access_token, response.email, response.user_id, response.role,
        )
        sessionManager.markUserLoggedIn()
    }

    suspend fun loginWithGoogle(idToken: String) {
        val response = centralApi.loginWithGoogle(idToken)
        if (response.role == ROLE_ADMIN) throw AdminLoginNotSupportedException()
        userIdentityStore.saveUserSession(
            response.access_token, response.email, response.user_id, response.role,
        )
        sessionManager.markUserLoggedIn()
    }

    private companion object {
        const val ROLE_ADMIN = "admin"
    }

    fun isUserAuthenticated(): Boolean = sessionManager.isUserAuthenticated()

    fun getUserEmail(): String? = userIdentityStore.getUserEmail()

    /**
     * Full logout: clears user identity AND agent-connection tokens.
     */
    fun userLogout() {
        sessionManager.userLogout()
    }

    // -- Agent connection (per-agent) ----------------------------------------

    suspend fun connectAgent(agentUrl: String, username: String, password: String) {
        val response = restApi.login(agentUrl, username, password)
        // Clear any relay-mode keys from a previous session so that
        // getConnectionMode() returns DIRECT after a direct login.
        tokenStore.clear()
        // Persist first, then flip the observable state. Ordering matters
        // so that any observer that races on FullyConnected can read the
        // tokens it needs without seeing a half-initialised session.
        tokenStore.saveAgentUrl(agentUrl)
        tokenStore.saveTokens(response.access_token, response.refresh_token)
        sessionManager.markAgentConnected()

        // Auto-save to ConnectionStore for multi-connection management
        connectionStore.captureActiveProfile(
            tokenStore,
            label = "Direct: $agentUrl",
        )
    }

    /**
     * Disconnect from the current agent but keep the user identity.
     * Transitions FullyConnected → UserOnly.
     */
    fun disconnectAgent() {
        sessionManager.agentDisconnect()
    }

    /**
     * Legacy tear-down. Routes through [SessionManager.forceLogout]
     * so that token clearing and the state-flip happen in one critical
     * section.
     */
    fun logout() {
        sessionManager.forceLogout()
    }

    fun isLoggedIn(): Boolean = sessionManager.isLoggedIn()

    fun getAgentUrl(): String? = tokenStore.getAgentUrl()

    fun getUsername(): String? = tokenStore.getUsername()
}
