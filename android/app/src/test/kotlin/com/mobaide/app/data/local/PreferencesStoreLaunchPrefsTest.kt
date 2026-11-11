package com.mobaide.app.data.local

import android.content.Context
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
 * Unit tests for the [PreferencesStore] launch-prefs blobs against a real
 * DataStore file (the [PreferencesStoreRecentChordsTest] pattern): the Claude
 * Code and Auto Scan buckets round-trip [ClaudeLaunchPrefs] including the
 * skip-permissions / no-memory / plan-mode toggles, and writing one bucket
 * never mutates the other — the per-kind isolation the wizard relies on.
 *
 * The file-scope `preferencesDataStore` delegate caches one DataStore instance
 * per sandboxed class run, so every test here writes before it reads — no
 * assertion depends on a key being unset.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PreferencesStoreLaunchPrefsTest {

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
    fun autoScanBucket_roundTripsAllFields_includingPlanMode() = runTest {
        val prefs = ClaudeLaunchPrefs(
            model = "sonnet",
            effort = "low",
            appendSystemPrompt = "scan sys",
            skipPermissions = false,
            noMemory = true,
            planMode = true,
        )

        store.setLastUsedAutoScanOptions(prefs)

        assertEquals(prefs, store.getLastUsedAutoScanOptions())
    }

    @Test
    fun claudeAndAutoScanBuckets_areIsolated() = runTest {
        val claude = ClaudeLaunchPrefs(model = "opus", skipPermissions = true)
        val autoScan = ClaudeLaunchPrefs(model = "haiku", skipPermissions = false)

        store.setLastUsedClaudeOptions(claude)
        store.setLastUsedAutoScanOptions(autoScan)

        assertEquals(
            "an Auto Scan write must not pre-fill the Claude Code sheet",
            claude,
            store.getLastUsedClaudeOptions(),
        )
        assertEquals(
            "a Claude Code write must not pre-fill the Auto Scan sheet",
            autoScan,
            store.getLastUsedAutoScanOptions(),
        )
    }
}
