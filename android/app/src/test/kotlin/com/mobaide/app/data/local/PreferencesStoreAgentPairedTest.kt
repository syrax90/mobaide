package com.mobaide.app.data.local

import android.content.Context
import java.io.File
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * The `agent_paired` conversion keys against a real DataStore file: outcomes
 * are per account, the armed timestamp is write-once, and the sign-out sweep
 * for recent custom paths leaves both untouched.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PreferencesStoreAgentPairedTest {

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

    // Keys are unique per test: the DataStore instance behind the file-scope
    // delegate outlives the per-test file deletion, so state written by one
    // test is still visible to the next.

    @Test
    fun outcome_isKeyedPerAccount() = runTest {
        assertNull(store.getAgentPairedOutcome("keyed-u1"))

        store.setAgentPairedOutcome("keyed-u1", "logged")

        assertEquals("logged", store.getAgentPairedOutcome("keyed-u1"))
        assertNull(store.getAgentPairedOutcome("keyed-u2"))
    }

    @Test
    fun armConversionIfUnset_keepsTheFirstValue() = runTest {
        val first = store.armConversionIfUnset(1_000L)

        assertEquals(first, store.armConversionIfUnset(2_000L))
        assertEquals(first, store.getConversionArmedAt())
    }

    @Test
    fun signOutSweepOfRecentPaths_leavesTheConversionKeys() = runTest {
        store.setAgentPairedOutcome("sweep-u1", "logged")
        val armedAt = store.armConversionIfUnset(1_000L)
        store.addRecentCustomPath("sweep-agent", "/tmp/a")

        store.removeAllRecentCustomPaths()

        assertEquals("logged", store.getAgentPairedOutcome("sweep-u1"))
        assertEquals(armedAt, store.getConversionArmedAt())
        assertEquals(emptyList<String>(), store.getRecentCustomPaths("sweep-agent"))
    }
}
