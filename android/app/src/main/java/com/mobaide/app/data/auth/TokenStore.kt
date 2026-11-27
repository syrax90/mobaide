package com.mobaide.app.data.auth

import android.content.Context
import android.content.SharedPreferences
import android.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long

class TokenStore(context: Context) {

    private val prefs: SharedPreferences = openEncryptedPrefs(context, EncryptedPrefsFiles.AUTH)

    init {
        clearCredentialsStoredWithoutRelay()
    }

    private fun clearCredentialsStoredWithoutRelay() {
        if (prefs.getString(KEY_CONNECTION_MODE, null) != MODE_RELAY && prefs.all.isNotEmpty()) clear()
    }

    fun saveTokens(accessToken: String, refreshToken: String) {
        prefs.edit()
            .putString(KEY_ACCESS_TOKEN, accessToken)
            .putString(KEY_REFRESH_TOKEN, refreshToken)
            .apply()
    }

    fun getAccessToken(): String? = prefs.getString(KEY_ACCESS_TOKEN, null)

    fun getRefreshToken(): String? = prefs.getString(KEY_REFRESH_TOKEN, null)

    fun saveRelayCredentials(
        serverUrl: String,
        agentId: String,
        pairingId: String,
        deviceId: String,
        accessToken: String,
        refreshToken: String,
    ) {
        prefs.edit()
            .putString(KEY_CONNECTION_MODE, MODE_RELAY)
            .putString(KEY_RELAY_SERVER_URL, serverUrl.trimEnd('/'))
            .putString(KEY_AGENT_ID, agentId)
            .putString(KEY_PAIRING_ID, pairingId)
            .putString(KEY_DEVICE_ID, deviceId)
            .putString(KEY_ACCESS_TOKEN, accessToken)
            .putString(KEY_REFRESH_TOKEN, refreshToken)
            .apply()
    }

    fun getRelayServerUrl(): String? = prefs.getString(KEY_RELAY_SERVER_URL, null)

    fun getAgentId(): String? = prefs.getString(KEY_AGENT_ID, null)

    fun getPairingId(): String? = prefs.getString(KEY_PAIRING_ID, null)

    fun getDeviceId(): String? = prefs.getString(KEY_DEVICE_ID, null)

    /**
     * Return the base URL for REST API calls: the relay's proxy URL for the
     * connected agent (e.g. `https://relay.example.com/proxy/abc123`), or
     * `null` when no agent is connected.
     */
    fun getEffectiveBaseUrl(): String? {
        val server = getRelayServerUrl() ?: return null
        val agentId = getAgentId() ?: return null
        return "$server/proxy/$agentId"
    }

    /**
     * Return true only when all values required to silently recover a session
     * are present.
     */
    fun isLoggedIn(): Boolean =
        getAccessToken() != null &&
            getRefreshToken() != null &&
            getRelayServerUrl() != null &&
            getAgentId() != null

    fun clear() {
        prefs.edit().clear().apply()
    }

    fun isAccessTokenExpiringSoon(): Boolean {
        val token = getAccessToken() ?: return true
        return try {
            val payload = token.split(".")[1]
            val decoded = Base64.decode(payload, Base64.URL_SAFE or Base64.NO_PADDING)
            val json = Json.parseToJsonElement(String(decoded)).jsonObject
            // Tokens without an `exp` claim never expire — the server issues
            // these when *_TOKEN_EXPIRE_MINUTES=0. Don't trigger a refresh.
            val exp = json["exp"]?.jsonPrimitive?.long ?: return false
            val nowSeconds = System.currentTimeMillis() / 1000
            exp - nowSeconds < 60
        } catch (_: Exception) {
            true
        }
    }

    companion object {
        private const val KEY_ACCESS_TOKEN = "access_token"
        private const val KEY_REFRESH_TOKEN = "refresh_token"
        private const val KEY_CONNECTION_MODE = "connection_mode"
        private const val MODE_RELAY = "relay"
        private const val KEY_RELAY_SERVER_URL = "relay_server_url"
        private const val KEY_AGENT_ID = "agent_id"
        private const val KEY_PAIRING_ID = "pairing_id"
        private const val KEY_DEVICE_ID = "device_id"
    }
}
