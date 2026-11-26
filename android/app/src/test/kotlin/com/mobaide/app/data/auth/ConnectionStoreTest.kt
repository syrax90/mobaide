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
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * Reads a profile list stored by an earlier app version: profiles of the
 * removed direct mode are dropped, relay profiles stay readable.
 *
 * Robolectric has no AndroidKeyStore, so [openEncryptedPrefs] is replaced by
 * plain prefs over the same file.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ConnectionStoreTest {

    private lateinit var context: Context
    private lateinit var prefs: SharedPreferences

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        context.deleteSharedPreferences(EncryptedPrefsFiles.CONNECTIONS)
        prefs = context.getSharedPreferences(EncryptedPrefsFiles.CONNECTIONS, Context.MODE_PRIVATE)
        mockkStatic(ENCRYPTED_PREFS_FILE_CLASS)
        every { openEncryptedPrefs(any(), EncryptedPrefsFiles.CONNECTIONS, any()) } returns prefs
    }

    @After
    fun tearDown() {
        unmockkStatic(ENCRYPTED_PREFS_FILE_CLASS)
        context.deleteSharedPreferences(EncryptedPrefsFiles.CONNECTIONS)
    }

    private fun seedProfilesOfAnEarlierVersion(activeId: String) {
        prefs.edit()
            .putString(
                "profiles",
                """
                [
                  {"id":"direct-1","label":"Direct: https://agent.local","mode":"direct",
                   "agentUrl":"https://agent.local","accessToken":"direct-access",
                   "refreshToken":"direct-refresh","createdAt":1},
                  {"id":"relay-1","label":"Relay: agent-abc","mode":"relay","agentUrl":null,
                   "relayServerUrl":"https://relay.example.com","agentId":"agent-abc",
                   "pairingId":"pair-1","deviceId":"dev-1","accessToken":"relay-access",
                   "refreshToken":"relay-refresh","createdAt":2}
                ]
                """.trimIndent(),
            )
            .putString("active_profile_id", activeId)
            .commit()
    }

    @Test
    fun listProfiles_keepsOnlyRelayProfiles_andRemovesTheOthersFromStorage() {
        seedProfilesOfAnEarlierVersion(activeId = "direct-1")
        val store = ConnectionStore(context)

        val profiles = store.listProfiles()

        assertEquals(listOf("relay-1"), profiles.map { it.id })
        assertEquals("agent-abc", profiles.single().agentId)
        assertEquals("relay-access", profiles.single().accessToken)
        val stored = prefs.getString("profiles", null).orEmpty()
        assertFalse(stored.contains("direct-access"))
        assertFalse(stored.contains("direct-refresh"))
        assertFalse(stored.contains("agent.local"))
        // The active profile was dropped, so no profile is active.
        assertNull(store.getActiveProfileId())
    }

    @Test
    fun listProfiles_keepsAnActiveRelayProfileActive() {
        seedProfilesOfAnEarlierVersion(activeId = "relay-1")
        val store = ConnectionStore(context)

        store.listProfiles()

        assertEquals("relay-1", store.getActiveProfileId())
    }

    private companion object {
        const val ENCRYPTED_PREFS_FILE_CLASS = "com.mobaide.app.data.auth.EncryptedPrefsKt"
    }
}
