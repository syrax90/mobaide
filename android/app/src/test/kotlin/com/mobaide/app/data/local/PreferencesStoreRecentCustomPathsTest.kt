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
 * Unit tests for the [PreferencesStore] recent-custom-path MRU against a real
 * DataStore file. The file-scope `preferencesDataStore` delegate caches one
 * DataStore instance per sandboxed class run, so its in-memory state outlives
 * the on-disk file deletion in setUp/tearDown.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PreferencesStoreRecentCustomPathsTest {

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
    fun recents_returnNewestFirst() = runTest {
        store.addRecentCustomPath("paths-order", "/tmp/a")
        store.addRecentCustomPath("paths-order", "/tmp/b")
        store.addRecentCustomPath("paths-order", "/tmp/c")

        assertEquals(
            listOf("/tmp/c", "/tmp/b", "/tmp/a"),
            store.getRecentCustomPaths("paths-order"),
        )
    }

    @Test
    fun reAdd_bumpsToFront_withoutDuplicating() = runTest {
        store.addRecentCustomPath("paths-bump", "/tmp/a")
        store.addRecentCustomPath("paths-bump", "/tmp/b")
        store.addRecentCustomPath("paths-bump", "/tmp/a")

        assertEquals(
            "relaunching a path must bump it to the front, not duplicate it",
            listOf("/tmp/a", "/tmp/b"),
            store.getRecentCustomPaths("paths-bump"),
        )
    }

    @Test
    fun addBeyondCap_evictsOldest() = runTest {
        val paths = (1..PreferencesStore.MAX_RECENT_CUSTOM_PATHS + 1).map { "/tmp/p$it" }
        paths.forEach { store.addRecentCustomPath("paths-cap", it) }

        val recents = store.getRecentCustomPaths("paths-cap")
        assertEquals(PreferencesStore.MAX_RECENT_CUSTOM_PATHS, recents.size)
        assertFalse(
            "the oldest path must fall off once the cap is exceeded",
            recents.contains("/tmp/p1"),
        )
        assertEquals(paths.drop(1).reversed(), recents)
    }

    @Test
    fun blankPath_isIgnored() = runTest {
        store.addRecentCustomPath("paths-blank", "")
        store.addRecentCustomPath("paths-blank", "   ")

        assertEquals(emptyList<String>(), store.getRecentCustomPaths("paths-blank"))
    }

    @Test
    fun add_storesTheTrimmedPath_andAnUntrimmedRepeatBumpsWithoutDuplicating() = runTest {
        store.addRecentCustomPath("paths-trim", "  /tmp/x  ")
        assertEquals(
            "the chip must render the path the agent was given, not the typed padding",
            listOf("/tmp/x"),
            store.getRecentCustomPaths("paths-trim"),
        )

        store.addRecentCustomPath("paths-trim", "/tmp/y")
        store.addRecentCustomPath("paths-trim", "  /tmp/x  ")

        assertEquals(
            "surrounding whitespace must not fork a second entry for one directory",
            listOf("/tmp/x", "/tmp/y"),
            store.getRecentCustomPaths("paths-trim"),
        )
    }

    @Test
    fun agentBuckets_areIsolated() = runTest {
        store.addRecentCustomPath("paths-agent-a", "/tmp/a")
        store.addRecentCustomPath("paths-agent-b", "/tmp/b")

        assertEquals(listOf("/tmp/a"), store.getRecentCustomPaths("paths-agent-a"))
        assertEquals(listOf("/tmp/b"), store.getRecentCustomPaths("paths-agent-b"))
    }

    @Test
    fun remove_dropsTheMatch_andKeepsSurvivorsInMruOrder() = runTest {
        listOf("/tmp/a", "/tmp/b", "/tmp/c").forEach {
            store.addRecentCustomPath("paths-remove", it)
        }

        store.removeRecentCustomPath("paths-remove", "/tmp/b")

        assertEquals(
            "removing from the middle must not reshuffle the survivors",
            listOf("/tmp/c", "/tmp/a"),
            store.getRecentCustomPaths("paths-remove"),
        )
    }

    @Test
    fun remove_ignoresAnEntryTheArgumentIsOnlyAPrefixOf() = runTest {
        store.addRecentCustomPath("paths-prefix", "/srv/work")
        store.addRecentCustomPath("paths-prefix", "/srv/workspace")

        store.removeRecentCustomPath("paths-prefix", "/srv/work")

        assertEquals(
            "removal is exact-match: a shorter path must not take the longer one with it",
            listOf("/srv/workspace"),
            store.getRecentCustomPaths("paths-prefix"),
        )
    }

    @Test
    fun remove_acceptsAnUntrimmedArgument_forATrimmedEntry() = runTest {
        store.addRecentCustomPath("paths-remove-trim", "/tmp/x")
        store.addRecentCustomPath("paths-remove-trim", "/tmp/y")

        store.removeRecentCustomPath("paths-remove-trim", "  /tmp/x  ")

        assertEquals(listOf("/tmp/y"), store.getRecentCustomPaths("paths-remove-trim"))
    }

    @Test
    fun remove_isScopedToOneAgentBucket() = runTest {
        store.addRecentCustomPath("paths-scope-a", "/shared")
        store.addRecentCustomPath("paths-scope-b", "/shared")

        store.removeRecentCustomPath("paths-scope-a", "/shared")

        assertEquals(emptyList<String>(), store.getRecentCustomPaths("paths-scope-a"))
        assertEquals(
            "the same path under another agent must survive",
            listOf("/shared"),
            store.getRecentCustomPaths("paths-scope-b"),
        )
    }

    @Test
    fun removingTheLastEntry_emptiesTheBucket_andItStaysWritable() = runTest {
        store.addRecentCustomPath("paths-last", "/tmp/only")

        store.removeRecentCustomPath("paths-last", "/tmp/only")
        assertEquals(emptyList<String>(), store.getRecentCustomPaths("paths-last"))

        store.addRecentCustomPath("paths-last", "/tmp/fresh")
        assertEquals(listOf("/tmp/fresh"), store.getRecentCustomPaths("paths-last"))
    }

    @Test
    fun removeAll_clearsEveryAgentBucket_andSparesTheNeighbouringKeys() = runTest {
        // Sign-out sweeps by prefix because the current agent key is already
        // gone by then — that scan must not take unrelated preferences with it.
        // The DataStore instance is shared for this whole class run, so this
        // test asserts only about keys it uniquely owns.
        store.addRecentCustomPath("paths-purge-a", "/srv/one")
        store.addRecentCustomPath("paths-purge-a", "/srv/two")
        store.addRecentCustomPath("paths-purge-b", "/home/alice/work")
        store.addRecentShortcutChord("paths-purge-a", "ctrl+c")
        store.setLastUsedProjectName("paths-purge-project")

        store.removeAllRecentCustomPaths()

        assertEquals(emptyList<String>(), store.getRecentCustomPaths("paths-purge-a"))
        assertEquals(
            "a bucket for an agent other than the current one must go too",
            emptyList<String>(),
            store.getRecentCustomPaths("paths-purge-b"),
        )
        assertEquals(
            "the shortcut-chord family shares the double-colon shape and must survive",
            listOf("ctrl+c"),
            store.getRecentShortcutChords("paths-purge-a"),
        )
        assertEquals("paths-purge-project", store.getLastUsedProjectName())
    }

    @Test
    fun remove_ofAnAbsentPath_isANoOp() = runTest {
        store.removeRecentCustomPath("paths-absent", "/tmp/never-written")
        assertEquals(emptyList<String>(), store.getRecentCustomPaths("paths-absent"))

        store.addRecentCustomPath("paths-absent", "/tmp/first")
        store.removeRecentCustomPath("paths-absent", "/tmp/not-stored")

        assertEquals(listOf("/tmp/first"), store.getRecentCustomPaths("paths-absent"))
    }
}
