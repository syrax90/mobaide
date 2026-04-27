package com.mobaide.app.data.auth

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys

/**
 * Store the encrypted **user identity** — the central-server JWT, email,
 * userId, and role. Completely separate from [TokenStore], which holds
 * **agent-connection** tokens.
 *
 * User identity persists across agent switches: connecting to a different
 * agent clears [TokenStore] but never touches this store.
 */
class UserIdentityStore(context: Context) {

    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        "mobaide_user_identity",
        MasterKeys.getOrCreate(MasterKeys.AES256_GCM_SPEC),
        context,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    fun saveUserSession(accessToken: String, email: String, userId: String, role: String) {
        prefs.edit()
            .putString(KEY_ACCESS_TOKEN, accessToken)
            .putString(KEY_EMAIL, email)
            .putString(KEY_USER_ID, userId)
            .putString(KEY_ROLE, role)
            .apply()
    }

    fun getUserAccessToken(): String? = prefs.getString(KEY_ACCESS_TOKEN, null)

    fun getUserEmail(): String? = prefs.getString(KEY_EMAIL, null)

    fun getUserId(): String? = prefs.getString(KEY_USER_ID, null)

    fun getUserRole(): String? = prefs.getString(KEY_ROLE, null)

    fun isUserLoggedIn(): Boolean = getUserAccessToken() != null

    fun clearUserSession() {
        prefs.edit().clear().apply()
    }

    companion object {
        private const val KEY_ACCESS_TOKEN = "user_access_token"
        private const val KEY_EMAIL = "user_email"
        private const val KEY_USER_ID = "user_id"
        private const val KEY_ROLE = "user_role"
        // Reserved for future use when the server returns a refresh token
        @Suppress("unused")
        private const val KEY_REFRESH_TOKEN = "user_refresh_token"
    }
}
