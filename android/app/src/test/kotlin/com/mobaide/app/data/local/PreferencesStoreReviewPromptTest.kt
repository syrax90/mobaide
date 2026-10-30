package com.mobaide.app.data.local

import android.content.Context
import com.mobaide.app.data.review.ReviewPromptState
import java.io.File
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * The file-scope `preferencesDataStore` delegate caches one DataStore per
 * sandboxed class run and survives the on-disk cleanup in setUp/tearDown. The
 * whole sequence therefore lives in a single test, where the ordering is the
 * test's own rather than JUnit's.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PreferencesStoreReviewPromptTest {

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
    fun reviewCounters_countUp_andARequestStampsTheTimeAndResetsTheGoodSessions() = runTest {
        store.incrementReviewLaunchCount()
        store.incrementReviewLaunchCount()

        assertEquals(
            "absent keys read as zero and no stamp on a fresh install",
            ReviewPromptState(launchCount = 2, goodSessions = 1, lastRequestedAtMs = null),
            store.incrementReviewGoodSessions(),
        )
        assertEquals(
            ReviewPromptState(launchCount = 2, goodSessions = 2, lastRequestedAtMs = null),
            store.incrementReviewGoodSessions(),
        )

        store.markReviewRequested(STAMP_MS)

        assertEquals(
            ReviewPromptState(launchCount = 2, goodSessions = 1, lastRequestedAtMs = STAMP_MS),
            store.incrementReviewGoodSessions(),
        )
    }

    private companion object {
        const val STAMP_MS = 1_793_275_200_000L
    }
}
