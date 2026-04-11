package com.claudemobile.app.data.auth

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

    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        "claude_mobile_auth",
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

    fun saveServerUrl(url: String) {
        prefs.edit().putString(KEY_SERVER_URL, url.trimEnd('/')).apply()
    }

    fun getServerUrl(): String? = prefs.getString(KEY_SERVER_URL, null)

    /**
     * "Logged in" means we have **all three** persisted values required to
     * recover a session silently: access token, refresh token, and server
     * URL. Dropping the refresh-token check here meant a previously
     * invalidated session (access-token-present, refresh-token-cleared) was
     * still reported as logged in, and the UI would navigate to the
     * authenticated graph before firing doomed API calls.
     */
    fun isLoggedIn(): Boolean =
        getAccessToken() != null &&
            getRefreshToken() != null &&
            getServerUrl() != null

    fun clear() {
        prefs.edit().clear().apply()
    }

    fun isAccessTokenExpiringSoon(): Boolean {
        val token = getAccessToken() ?: return true
        return try {
            val payload = token.split(".")[1]
            val decoded = Base64.decode(payload, Base64.URL_SAFE or Base64.NO_PADDING)
            val json = Json.parseToJsonElement(String(decoded)).jsonObject
            val exp = json["exp"]?.jsonPrimitive?.long ?: return true
            val nowSeconds = System.currentTimeMillis() / 1000
            exp - nowSeconds < 60
        } catch (_: Exception) {
            true
        }
    }

    fun getUsername(): String? {
        val token = getAccessToken() ?: return null
        return try {
            val payload = token.split(".")[1]
            val decoded = Base64.decode(payload, Base64.URL_SAFE or Base64.NO_PADDING)
            val json = Json.parseToJsonElement(String(decoded)).jsonObject
            json["sub"]?.jsonPrimitive?.content
        } catch (_: Exception) {
            null
        }
    }

    companion object {
        private const val KEY_ACCESS_TOKEN = "access_token"
        private const val KEY_REFRESH_TOKEN = "refresh_token"
        private const val KEY_SERVER_URL = "server_url"
    }
}
