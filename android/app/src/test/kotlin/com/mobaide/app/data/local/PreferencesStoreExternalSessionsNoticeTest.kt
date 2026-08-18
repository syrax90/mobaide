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
 * The file-scope `preferencesDataStore` delegate caches one DataStore per
 * sandboxed class run and survives the on-disk cleanup in setUp/tearDown. Both
 * halves of the contract therefore live in a single test, where the ordering
 * is the test's own rather than JUnit's.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PreferencesStoreExternalSessionsNoticeTest {

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
    fun noticeFlag_defaultsToNotDismissed_thenSetterPersistsDismissed() = runTest {
        assertFalse(
            "an absent key must read as not-dismissed so a fresh install sees the notice",
            store.externalSessionsNoticeDismissed.first(),
        )

        store.setExternalSessionsNoticeDismissed()

        assertTrue(store.externalSessionsNoticeDismissed.first())
    }
}
