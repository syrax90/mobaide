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
 * The notice before the first transcript-cleanup opt-in is per device: it
 * stays unseen until acknowledged. The on/off choice itself lives on the agent.
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
    fun cleanupNotice_isUnseenUntilAcknowledged() = runTest {
        assertFalse(
            "an absent key must read as unseen so the first opt-in shows the notice",
            store.voiceCleanupNoticeSeen.first(),
        )

        store.setVoiceCleanupNoticeSeen()

        assertTrue(store.voiceCleanupNoticeSeen.first())
    }
}
