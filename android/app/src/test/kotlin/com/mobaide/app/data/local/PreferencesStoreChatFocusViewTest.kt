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

/** The chat focus view is one per-device setting, on until the user turns it off. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PreferencesStoreChatFocusViewTest {

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
    fun focusView_isOnUntilTurnedOff_andCanBeTurnedBackOn() = runTest {
        assertTrue("an absent key must read as on", store.chatFocusView.first())

        store.setChatFocusView(false)
        assertFalse(store.chatFocusView.first())

        store.setChatFocusView(true)
        assertTrue(store.chatFocusView.first())
    }
}
