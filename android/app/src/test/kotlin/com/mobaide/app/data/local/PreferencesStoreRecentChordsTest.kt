package com.mobaide.app.data.local

import android.content.Context
import java.io.File
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * Unit tests for the [PreferencesStore] recent-shortcut-chord MRU against a
 * real DataStore file (the [com.mobaide.app.data.auth.DeviceIdentityStoreTest]
 * pattern): newest-first ordering, re-add bumps without duplicating, the
 * MAX_RECENT_SHORTCUT_CHORDS cap evicts the oldest entry, and per-session-kind
 * buckets never bleed into each other.
 *
 * The file-scope `preferencesDataStore` delegate caches one DataStore instance
 * per sandboxed class run, so its in-memory state outlives the on-disk file
 * deletion in setUp/tearDown. Each test therefore writes to its own bucket key
 * — cross-test isolation comes from the keyspace, the file cleanup just keeps
 * the sandbox tidy.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PreferencesStoreRecentChordsTest {

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
    fun reAdd_bumpsToFront_withoutDuplicating() = runTest {
        store.addRecentShortcutChord("mru-bump", "ctrl+a")
        store.addRecentShortcutChord("mru-bump", "ctrl+b")
        store.addRecentShortcutChord("mru-bump", "ctrl+a")

        assertEquals(
            "re-sending a chord must bump it to the front, not duplicate it",
            listOf("ctrl+a", "ctrl+b"),
            store.getRecentShortcutChords("mru-bump"),
        )
    }

    @Test
    fun addBeyondCap_evictsOldest() = runTest {
        val tokens = ('a'..'k').map { "ctrl+$it" }
        assertEquals(PreferencesStore.MAX_RECENT_SHORTCUT_CHORDS + 1, tokens.size)
        tokens.forEach { store.addRecentShortcutChord("mru-cap", it) }

        val recents = store.getRecentShortcutChords("mru-cap")
        assertEquals(PreferencesStore.MAX_RECENT_SHORTCUT_CHORDS, recents.size)
        assertFalse(
            "the oldest chord must fall off once the cap is exceeded",
            recents.contains("ctrl+a"),
        )
        assertEquals(tokens.drop(1).reversed(), recents)
    }

    @Test
    fun sessionKindBuckets_areIsolated() = runTest {
        store.addRecentShortcutChord("claude", "ctrl+x")
        store.addRecentShortcutChord("codex", "ctrl+y")

        assertEquals(listOf("ctrl+x"), store.getRecentShortcutChords("claude"))
        assertEquals(listOf("ctrl+y"), store.getRecentShortcutChords("codex"))
    }

    @Test
    fun blankToken_isIgnored() = runTest {
        store.addRecentShortcutChord("mru-blank", "")
        store.addRecentShortcutChord("mru-blank", "   ")

        assertEquals(emptyList<String>(), store.getRecentShortcutChords("mru-blank"))
    }
}
