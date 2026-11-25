package com.mobaide.app.data.auth

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys
import java.io.IOException
import java.security.GeneralSecurityException
import java.security.ProviderException

/**
 * Every `EncryptedSharedPreferences` file the app owns. Each file holds a Tink
 * keyset wrapped by an Android Keystore key, and Keystore keys are never
 * restored, so every name here must be excluded in `backup_rules.xml` and in
 * both sections of `data_extraction_rules.xml`.
 */
object EncryptedPrefsFiles {
    const val AUTH = "mobaide_auth"
    const val CONNECTIONS = "mobaide_connections"
    const val USER_IDENTITY = "mobaide_user_identity"

    val ALL = listOf(AUTH, CONNECTIONS, USER_IDENTITY)
}

private const val TAG = "EncryptedPrefs"

/**
 * Open an encrypted prefs file, and replace it with an empty one when it
 * cannot be decrypted (a file restored from a backup or a device transfer).
 * The Keystore master key is shared by all stores, so it is never deleted.
 */
fun openEncryptedPrefs(
    context: Context,
    fileName: String,
    create: (Context, String) -> SharedPreferences = ::createEncryptedPrefs,
): SharedPreferences {
    val firstFailure = try {
        return create(context, fileName)
    } catch (e: Exception) {
        if (!e.isKeysetFailure()) throw e
        e
    }
    // Some OEM Keystores fail transiently, so retry once before deleting data.
    try {
        return create(context, fileName)
    } catch (e: Exception) {
        if (!e.isKeysetFailure()) throw e
        Log.w(
            TAG,
            "Cannot open $fileName (${firstFailure.javaClass.name}, then ${e.javaClass.name}); " +
                "deleting it and starting empty",
        )
    }
    context.deleteSharedPreferences(fileName)
    return create(context, fileName)
}

private fun Exception.isKeysetFailure(): Boolean =
    this is GeneralSecurityException || this is IOException || this is ProviderException

private fun createEncryptedPrefs(context: Context, fileName: String): SharedPreferences =
    EncryptedSharedPreferences.create(
        fileName,
        MasterKeys.getOrCreate(MasterKeys.AES256_GCM_SPEC),
        context,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )
