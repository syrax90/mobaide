package com.claudemobile.app.data.repository

import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.network.RestApi

class AuthRepository(
    private val restApi: RestApi,
    private val tokenStore: TokenStore,
) {
    suspend fun login(serverUrl: String, username: String, password: String) {
        val response = restApi.login(serverUrl, username, password)
        tokenStore.saveServerUrl(serverUrl)
        tokenStore.saveTokens(response.access_token, response.refresh_token)
    }

    fun logout() {
        tokenStore.clear()
    }

    fun isLoggedIn(): Boolean = tokenStore.isLoggedIn()

    fun getServerUrl(): String? = tokenStore.getServerUrl()

    fun getUsername(): String? = tokenStore.getUsername()
}
