package com.mobaide.app.data.local

import android.content.Context
import java.io.File
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * Transcript cleanup sends the text to the AI agent's provider, so it stays
 * off until turned on for one agent, and the notice before the first opt-in
 * stays unseen until acknowledged. The file-scope `preferencesDataStore`
 * delegate caches one DataStore per sandboxed class run, so the whole contract
 * lives in a single test, in the test's own order.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PreferencesStoreVoiceCleanupTest {

    private lateinit var context: Context
    private lateinit var store: PreferencesStore

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        clearDataStoreFile()
        store = PreferencesStore(context)
    }

    @After
    fun tearDown() {
        clearDataStoreFile()
    }

    private fun clearDataStoreFile() {
        File(context.filesDir, "datastore").listFiles()
            ?.filter { it.name.startsWith("mobaide_prefs") }
            ?.forEach { it.delete() }
    }

    @Test
    fun cleanup_isOffPerAgentUntilTurnedOn_andTheNoticeIsUnseenUntilAcknowledged() = runTest {
        assertFalse(store.voiceCleanup("agent-1").first())
        assertFalse(
            "an absent key must read as unseen so the first opt-in shows the notice",
            store.voiceCleanupNoticeSeen.first(),
        )

        store.setVoiceCleanup("agent-1", true)
        store.setVoiceCleanupNoticeSeen()

        assertTrue(store.voiceCleanup("agent-1").first())
        assertFalse("another agent stays off", store.voiceCleanup("agent-2").first())
        assertTrue(store.voiceCleanupNoticeSeen.first())

        store.setVoiceCleanup("agent-1", false)

        assertFalse(store.voiceCleanup("agent-1").first())
        assertTrue("turning cleanup off keeps the notice acknowledged", store.voiceCleanupNoticeSeen.first())
    }
}
