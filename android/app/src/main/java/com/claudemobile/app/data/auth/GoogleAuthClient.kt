package com.claudemobile.app.data.auth

import android.content.Context
import androidx.credentials.CredentialManager
import androidx.credentials.GetCredentialRequest
import androidx.credentials.exceptions.GetCredentialCancellationException
import androidx.credentials.exceptions.GetCredentialException
import androidx.credentials.exceptions.NoCredentialException
import com.claudemobile.app.BuildConfig
import com.google.android.libraries.identity.googleid.GetGoogleIdOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential

/**
 * Wrapper around the Credential Manager API for Google Sign-In.
 *
 * **Important:** [signIn] requires an **Activity** context because the
 * Credential Manager launches system UI to pick an account. In Compose,
 * `LocalContext.current` inside `setContent {}` IS the Activity — use it
 * directly when constructing this class.
 *
 * This class is NOT Hilt-injected (Activity-scoped context, created
 * per-use in the Composable layer).
 */
class GoogleAuthClient(private val context: Context) {

    private val credentialManager = CredentialManager.create(context)

    /**
     * Launch the Credential Manager one-tap Google Sign-In flow.
     *
     * Uses [filterByAuthorizedAccounts] = `false` so that even first-time
     * users see the account picker (setting it to `true` returns nothing
     * when the user hasn't previously authorized the app).
     */
    suspend fun signIn(): GoogleAuthResult {
        val googleIdOption = GetGoogleIdOption.Builder()
            .setFilterByAuthorizedAccounts(false)
            .setServerClientId(BuildConfig.GOOGLE_WEB_CLIENT_ID)
            .build()

        val request = GetCredentialRequest.Builder()
            .addCredentialOption(googleIdOption)
            .build()

        return try {
            val result = credentialManager.getCredential(
                request = request,
                context = context,
            )
            val googleIdTokenCredential =
                GoogleIdTokenCredential.createFrom(result.credential.data)
            GoogleAuthResult.Success(googleIdTokenCredential.idToken)
        } catch (_: GetCredentialCancellationException) {
            GoogleAuthResult.Cancelled
        } catch (_: NoCredentialException) {
            GoogleAuthResult.Error("No Google accounts available on this device")
        } catch (e: GetCredentialException) {
            GoogleAuthResult.Error("Google Sign-In failed: ${e.message}")
        }
    }
}

sealed interface GoogleAuthResult {
    data class Success(val idToken: String) : GoogleAuthResult
    data object Cancelled : GoogleAuthResult
    data class Error(val message: String) : GoogleAuthResult
}
