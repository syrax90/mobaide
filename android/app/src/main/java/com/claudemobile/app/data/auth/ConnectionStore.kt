package com.claudemobile.app.data.auth

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/**
 * Manages a list of saved connection profiles (direct + relay).
 *
 * One profile is "active" at a time — its credentials are loaded into
 * [TokenStore] for the rest of the app to use.
 */
class ConnectionStore(context: Context) {

    @Serializable
    data class ConnectionProfile(
        val id: String,
        val label: String,
        val mode: String, // "direct" | "relay"
        val agentUrl: String? = null,
        val relayServerUrl: String? = null,
        val agentId: String? = null,
        val pairingId: String? = null,
        val deviceId: String? = null,
        val accessToken: String? = null,
        val refreshToken: String? = null,
        val createdAt: Long = System.currentTimeMillis(),
    )

    private val json = Json { ignoreUnknownKeys = true }

    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        "claude_mobile_connections",
        MasterKeys.getOrCreate(MasterKeys.AES256_GCM_SPEC),
        context,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    fun listProfiles(): List<ConnectionProfile> {
        val raw = prefs.getString(KEY_PROFILES, null) ?: return emptyList()
        return try {
            json.decodeFromString<List<ConnectionProfile>>(raw)
        } catch (_: Exception) {
            emptyList()
        }
    }

    fun getActiveProfileId(): String? = prefs.getString(KEY_ACTIVE_ID, null)

    fun setActiveProfileId(id: String) {
        prefs.edit().putString(KEY_ACTIVE_ID, id).apply()
    }

    fun saveProfile(profile: ConnectionProfile) {
        val profiles = listProfiles().toMutableList()
        val idx = profiles.indexOfFirst { it.id == profile.id }
        if (idx >= 0) {
            profiles[idx] = profile
        } else {
            profiles.add(profile)
        }
        prefs.edit().putString(KEY_PROFILES, json.encodeToString(profiles)).apply()
    }

    fun removeProfile(id: String) {
        val profiles = listProfiles().filter { it.id != id }
        prefs.edit().putString(KEY_PROFILES, json.encodeToString(profiles)).apply()
        if (getActiveProfileId() == id) {
            prefs.edit().remove(KEY_ACTIVE_ID).apply()
        }
    }

    /**
     * Capture the current [TokenStore] state into a new [ConnectionProfile] and make it active.
     */
    fun captureActiveProfile(tokenStore: TokenStore, label: String): ConnectionProfile {
        val id = java.util.UUID.randomUUID().toString()
        val mode = tokenStore.getConnectionMode()
        val profile = ConnectionProfile(
            id = id,
            label = label,
            mode = if (mode == TokenStore.ConnectionMode.RELAY) "relay" else "direct",
            agentUrl = tokenStore.getAgentUrl(),
            relayServerUrl = tokenStore.getRelayServerUrl(),
            agentId = tokenStore.getAgentId(),
            pairingId = tokenStore.getPairingId(),
            deviceId = tokenStore.getDeviceId(),
            accessToken = tokenStore.getAccessToken(),
            refreshToken = tokenStore.getRefreshToken(),
        )
        saveProfile(profile)
        setActiveProfileId(id)
        return profile
    }

    /**
     * Load a saved profile's credentials into [TokenStore], making it
     * the active connection.
     */
    fun activateProfile(id: String, tokenStore: TokenStore) {
        val profile = listProfiles().find { it.id == id } ?: return

        tokenStore.clear()

        when (profile.mode) {
            "relay" -> {
                tokenStore.saveRelayCredentials(
                    serverUrl = profile.relayServerUrl ?: return,
                    agentId = profile.agentId ?: return,
                    pairingId = profile.pairingId ?: return,
                    deviceId = profile.deviceId ?: return,
                    accessToken = profile.accessToken ?: return,
                    refreshToken = profile.refreshToken ?: return,
                )
            }
            else -> {
                val agentUrl = profile.agentUrl ?: return
                val accessToken = profile.accessToken ?: return
                val refreshToken = profile.refreshToken ?: return
                tokenStore.saveAgentUrl(agentUrl)
                tokenStore.saveTokens(accessToken, refreshToken)
            }
        }

        setActiveProfileId(id)
    }

    companion object {
        private const val KEY_PROFILES = "profiles"
        private const val KEY_ACTIVE_ID = "active_profile_id"
    }
}
