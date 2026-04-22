package com.claudemobile.app.data.auth

import android.content.Context
import androidx.credentials.CredentialManager
import androidx.credentials.CredentialOption
import androidx.credentials.GetCredentialRequest
import androidx.credentials.GetCredentialResponse
import androidx.credentials.exceptions.GetCredentialCancellationException
import androidx.credentials.exceptions.GetCredentialException
import androidx.credentials.exceptions.GetCredentialProviderConfigurationException
import androidx.credentials.exceptions.NoCredentialException
import com.claudemobile.app.BuildConfig
import com.google.android.libraries.identity.googleid.GetGoogleIdOption
import com.google.android.libraries.identity.googleid.GetSignInWithGoogleOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
import com.google.android.libraries.identity.googleid.GoogleIdTokenParsingException
import java.security.MessageDigest
import java.security.SecureRandom

/**
 * Wrapper around the Credential Manager API for Google Sign-In.
 *
 * Implements the Android 2025+ recommended three-stage chained flow:
 *
 *  1. [GetGoogleIdOption] with `filterByAuthorizedAccounts=true` + auto-select —
 *     seamless re-entry for users who have previously authorized the app.
 *  2. On [NoCredentialException], retry with `filterByAuthorizedAccounts=false` —
 *     shows the account picker for any Google account on the device.
 *  3. On further [NoCredentialException], fall back to [GetSignInWithGoogleOption] —
 *     the classic "Sign in with Google" button flow. This path also works when the
 *     user has disabled the bottom-sheet "Sign in with Google" toggle in their
 *     Google account settings, and it gives the user an "Add Google account"
 *     entry point when no accounts exist.
 *
 * **Important:** [signIn] requires an **Activity** context because Credential
 * Manager launches system UI. In Compose, `LocalContext.current` inside
 * `setContent {}` IS the Activity — pass it directly.
 *
 * This class is NOT Hilt-injected (Activity-scoped context, created per-use in
 * the Composable layer).
 */
class GoogleAuthClient(private val context: Context) {

    private val credentialManager = CredentialManager.create(context)
    private val clientId: String = BuildConfig.GOOGLE_WEB_CLIENT_ID

    suspend fun signIn(): GoogleAuthResult {
        if (!isClientIdConfigured(clientId)) {
            return GoogleAuthResult.NotConfigured
        }

        val nonce = generateNonce()

        runStage(
            buildGoogleIdOption(filterByAuthorizedAccounts = true, nonce = nonce, autoSelect = true),
        )?.let { return it }

        runStage(
            buildGoogleIdOption(filterByAuthorizedAccounts = false, nonce = nonce, autoSelect = false),
        )?.let { return it }

        // Fallback: works when the bottom-sheet "Sign in with Google" toggle is disabled in Google
        // account settings, and offers an "Add account" entry point when no accounts exist.
        runStage(
            GetSignInWithGoogleOption.Builder(clientId)
                .setNonce(nonce)
                .build(),
        )?.let { return it }

        return GoogleAuthResult.NoAccounts(
            "No Google account found. Add a Google account in system settings and try again.",
        )
    }

    /**
     * Run a single credential-request stage.
     *
     * Returns `null` if the stage surfaced [NoCredentialException] (so the caller
     * can try the next stage). Returns a concrete [GoogleAuthResult] otherwise.
     */
    private suspend fun runStage(option: CredentialOption): GoogleAuthResult? {
        val request = GetCredentialRequest.Builder()
            .addCredentialOption(option)
            .build()
        return try {
            val response = credentialManager.getCredential(
                request = request,
                context = context,
            )
            extractIdToken(response)
        } catch (_: NoCredentialException) {
            null
        } catch (_: GetCredentialCancellationException) {
            GoogleAuthResult.Cancelled
        } catch (_: GetCredentialProviderConfigurationException) {
            GoogleAuthResult.PlayServicesUnavailable
        } catch (e: GetCredentialException) {
            GoogleAuthResult.Failed(e.message ?: e::class.java.simpleName)
        }
    }

    private fun extractIdToken(response: GetCredentialResponse): GoogleAuthResult {
        val credential = response.credential
        if (credential.type != GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL) {
            return GoogleAuthResult.Failed("Unexpected credential type: ${credential.type}")
        }
        return try {
            val idTokenCredential = GoogleIdTokenCredential.createFrom(credential.data)
            GoogleAuthResult.Success(idTokenCredential.idToken)
        } catch (_: GoogleIdTokenParsingException) {
            GoogleAuthResult.Failed("Invalid Google ID token")
        }
    }

    private fun buildGoogleIdOption(
        filterByAuthorizedAccounts: Boolean,
        nonce: String,
        autoSelect: Boolean,
    ): GetGoogleIdOption =
        GetGoogleIdOption.Builder()
            .setFilterByAuthorizedAccounts(filterByAuthorizedAccounts)
            .setServerClientId(clientId)
            .setNonce(nonce)
            .setAutoSelectEnabled(autoSelect)
            .build()

    companion object {
        /**
         * Return true if [BuildConfig.GOOGLE_WEB_CLIENT_ID] is wired to a real OAuth client.
         */
        fun isClientIdConfigured(clientId: String): Boolean {
            if (clientId.isBlank()) return false
            if (clientId.startsWith("YOUR_WEB_CLIENT_ID")) return false
            return clientId.endsWith(".apps.googleusercontent.com")
        }

        /**
         * Generate a SHA-256 hex nonce over 32 random bytes for Credential Manager replay protection.
         */
        fun generateNonce(): String {
            val bytes = ByteArray(32).also { SecureRandom().nextBytes(it) }
            val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
            return digest.joinToString(separator = "") { "%02x".format(it) }
        }
    }
}

sealed interface GoogleAuthResult {
    data class Success(val idToken: String) : GoogleAuthResult

    /** User explicitly dismissed the credential picker. */
    data object Cancelled : GoogleAuthResult

    /** Build-time `GOOGLE_WEB_CLIENT_ID` is missing or still the placeholder. */
    data object NotConfigured : GoogleAuthResult

    /** No Google account exists on the device (after all fallback stages exhausted). */
    data class NoAccounts(val message: String) : GoogleAuthResult

    /** Google Play services is missing/outdated/misconfigured. */
    data object PlayServicesUnavailable : GoogleAuthResult

    /** Any other credential-manager failure. */
    data class Failed(val message: String) : GoogleAuthResult
}
