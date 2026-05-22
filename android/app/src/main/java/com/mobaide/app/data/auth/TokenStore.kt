package com.mobaide.app.data.auth

import android.content.Context
import android.content.SharedPreferences
import android.util.Base64
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long

class TokenStore(context: Context) {

    enum class ConnectionMode { DIRECT, RELAY }

    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        "mobaide_auth",
        MasterKeys.getOrCreate(MasterKeys.AES256_GCM_SPEC),
        context,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    fun saveTokens(accessToken: String, refreshToken: String) {
        prefs.edit()
            .putString(KEY_ACCESS_TOKEN, accessToken)
            .putString(KEY_REFRESH_TOKEN, refreshToken)
            .apply()
    }

    fun getAccessToken(): String? = prefs.getString(KEY_ACCESS_TOKEN, null)

    fun getRefreshToken(): String? = prefs.getString(KEY_REFRESH_TOKEN, null)

    fun saveAgentUrl(url: String) {
        prefs.edit().putString(KEY_AGENT_URL, url.trimEnd('/')).apply()
    }

    fun getAgentUrl(): String? = prefs.getString(KEY_AGENT_URL, null)

    fun getConnectionMode(): ConnectionMode =
        if (prefs.getString(KEY_CONNECTION_MODE, null) == "relay") ConnectionMode.RELAY
        else ConnectionMode.DIRECT

    fun saveRelayCredentials(
        serverUrl: String,
        agentId: String,
        pairingId: String,
        deviceId: String,
        accessToken: String,
        refreshToken: String,
    ) {
        prefs.edit()
            .putString(KEY_CONNECTION_MODE, "relay")
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
     * Return the base URL for REST API calls, adapting to the active connection mode.
     * - DIRECT: agent URL (e.g. `https://my-agent.example.com`)
     * - RELAY:  proxy URL (e.g. `https://relay.example.com/proxy/abc123`)
     */
    fun getEffectiveBaseUrl(): String? = when (getConnectionMode()) {
        ConnectionMode.DIRECT -> getAgentUrl()
        ConnectionMode.RELAY -> {
            val server = getRelayServerUrl() ?: return null
            val agentId = getAgentId() ?: return null
            "$server/proxy/$agentId"
        }
    }

    /**
     * Return true only when all values required to silently recover a session
     * are present for the active connection mode.
     */
    fun isLoggedIn(): Boolean = when (getConnectionMode()) {
        ConnectionMode.DIRECT ->
            getAccessToken() != null &&
                getRefreshToken() != null &&
                getAgentUrl() != null

        ConnectionMode.RELAY ->
            getAccessToken() != null &&
                getRefreshToken() != null &&
                getRelayServerUrl() != null &&
                getAgentId() != null
    }

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
        private const val KEY_AGENT_URL = "agent_url"
        private const val KEY_CONNECTION_MODE = "connection_mode"
        private const val KEY_RELAY_SERVER_URL = "relay_server_url"
        private const val KEY_AGENT_ID = "agent_id"
        private const val KEY_PAIRING_ID = "pairing_id"
        private const val KEY_DEVICE_ID = "device_id"
    }
}
