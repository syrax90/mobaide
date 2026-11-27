package com.mobaide.app.data.auth

import android.content.Context
import android.content.SharedPreferences
import io.mockk.every
import io.mockk.mockkStatic
import io.mockk.unmockkStatic
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * Opening [TokenStore] deletes credentials of the removed direct mode once
 * and keeps relay credentials.
 *
 * Robolectric has no AndroidKeyStore, so [openEncryptedPrefs] is replaced by
 * plain prefs over the same file.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TokenStoreTest {

    private lateinit var context: Context
    private lateinit var prefs: SharedPreferences

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        context.deleteSharedPreferences(EncryptedPrefsFiles.AUTH)
        prefs = context.getSharedPreferences(EncryptedPrefsFiles.AUTH, Context.MODE_PRIVATE)
        mockkStatic(ENCRYPTED_PREFS_FILE_CLASS)
        every { openEncryptedPrefs(any(), EncryptedPrefsFiles.AUTH, any()) } returns prefs
    }

    @After
    fun tearDown() {
        unmockkStatic(ENCRYPTED_PREFS_FILE_CLASS)
        context.deleteSharedPreferences(EncryptedPrefsFiles.AUTH)
    }

    private fun seed(vararg entries: Pair<String, String>) {
        val editor = prefs.edit()
        entries.forEach { (key, value) -> editor.putString(key, value) }
        editor.commit()
    }

    @Test
    fun credentialsStoredWithoutAMode_areDeletedOnOpen() {
        // The removed direct mode never wrote a mode value.
        seed(
            "agent_url" to "https://agent.local:8000",
            "access_token" to "direct-access",
            "refresh_token" to "direct-refresh",
        )

        val store = TokenStore(context)

        assertTrue(prefs.all.isEmpty())
        assertNull(store.getAccessToken())
        assertNull(store.getRefreshToken())
        assertFalse(store.isLoggedIn())
    }

    @Test
    fun credentialsStoredWithAnotherMode_areDeletedOnOpen() {
        seed(
            "connection_mode" to "direct",
            "agent_url" to "https://agent.local:8000",
            "access_token" to "direct-access",
            "refresh_token" to "direct-refresh",
        )

        val store = TokenStore(context)

        assertTrue(prefs.all.isEmpty())
        assertFalse(store.isLoggedIn())
    }

    @Test
    fun relayCredentials_surviveANewInstance() {
        TokenStore(context).saveRelayCredentials(
            serverUrl = "https://relay.example.com/",
            agentId = "agent-1",
            pairingId = "pair-1",
            deviceId = "dev-1",
            accessToken = "relay-access",
            refreshToken = "relay-refresh",
        )

        val reopened = TokenStore(context)

        assertTrue(reopened.isLoggedIn())
        assertEquals("relay-access", reopened.getAccessToken())
        assertEquals("relay-refresh", reopened.getRefreshToken())
        assertEquals("pair-1", reopened.getPairingId())
        assertEquals("dev-1", reopened.getDeviceId())
        assertEquals("https://relay.example.com/proxy/agent-1", reopened.getEffectiveBaseUrl())
    }

    @Test
    fun rotatedTokensOfARelaySession_surviveANewInstance() {
        val store = TokenStore(context)
        store.saveRelayCredentials(
            serverUrl = "https://relay.example.com",
            agentId = "agent-1",
            pairingId = "pair-1",
            deviceId = "dev-1",
            accessToken = "relay-access",
            refreshToken = "relay-refresh",
        )
        store.saveTokens("rotated-access", "rotated-refresh")

        val reopened = TokenStore(context)

        assertTrue(reopened.isLoggedIn())
        assertEquals("rotated-access", reopened.getAccessToken())
        assertEquals("rotated-refresh", reopened.getRefreshToken())
    }

    private companion object {
        const val ENCRYPTED_PREFS_FILE_CLASS = "com.mobaide.app.data.auth.EncryptedPrefsKt"
    }
}
