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

    fun saveUserSession(
        accessToken: String,
        email: String,
        userId: String,
        role: String,
        scheduledDeletionAt: String? = null,
    ) {
        prefs.edit()
            .putString(KEY_ACCESS_TOKEN, accessToken)
            .putString(KEY_EMAIL, email)
            .putString(KEY_USER_ID, userId)
            .putString(KEY_ROLE, role)
            .apply()
        setScheduledDeletionAt(scheduledDeletionAt)
    }

    fun getUserAccessToken(): String? = prefs.getString(KEY_ACCESS_TOKEN, null)

    fun getUserEmail(): String? = prefs.getString(KEY_EMAIL, null)

    fun getUserId(): String? = prefs.getString(KEY_USER_ID, null)

    fun getUserRole(): String? = prefs.getString(KEY_ROLE, null)

    /**
     * ISO-8601 timestamp the server reports as the scheduled-deletion final
     * purge date when the account is in the soft-delete grace window, or
     * `null` if the account is active. Persisted so the banner survives an
     * app restart between sign-in and cancel.
     */
    fun getScheduledDeletionAt(): String? = prefs.getString(KEY_SCHEDULED_DELETION_AT, null)

    fun setScheduledDeletionAt(value: String?) {
        prefs.edit()
            .apply {
                if (value.isNullOrEmpty()) remove(KEY_SCHEDULED_DELETION_AT)
                else putString(KEY_SCHEDULED_DELETION_AT, value)
            }
            .apply()
    }

    fun isUserLoggedIn(): Boolean = getUserAccessToken() != null

    fun clearUserSession() {
        prefs.edit().clear().apply()
    }

    companion object {
        private const val KEY_ACCESS_TOKEN = "user_access_token"
        private const val KEY_EMAIL = "user_email"
        private const val KEY_USER_ID = "user_id"
        private const val KEY_ROLE = "user_role"
        private const val KEY_SCHEDULED_DELETION_AT = "scheduled_deletion_at"
        // Reserved for future use when the server returns a refresh token
        @Suppress("unused")
        private const val KEY_REFRESH_TOKEN = "user_refresh_token"
    }
}
