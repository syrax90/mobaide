package com.mobaide.app.data.fs

import com.mobaide.app.data.network.dto.FsDirsResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The pure half of the folder suggestions: [PathCompletion] and the
 * [DirListingCache] reuse rule. Rule-for-rule mirror of web
 * `pathCompletion.test.ts` and iOS `PathCompletionTests`; the cache runs on a
 * hand-driven clock so TTL cases never sleep.
 */
class PathCompletionTest {

    private fun listing(path: String, dirs: List<String>, truncated: Boolean = false) =
        FsDirsResponse(path = path, dirs = dirs, truncated = truncated)

    private var now = 1_000_000L
    private val cache = DirListingCache { now }

    @Test
    fun split_listsTheDirectoryUpToTheLastSeparator() {
        assertEquals(PathCompletion.Query("/", ""), PathCompletion.split("/"))
        assertEquals(PathCompletion.Query("/", "Us"), PathCompletion.split("/Us"))
        assertEquals(PathCompletion.Query("/Users/", "pa"), PathCompletion.split("/Users/pa"))
        assertEquals(PathCompletion.Query("~/", "co"), PathCompletion.split("~/co"))
        assertEquals(PathCompletion.Query("C:\\Users\\", "Pa"), PathCompletion.split("C:\\Users\\Pa"))
        assertEquals(PathCompletion.Query("c:/work/", "x"), PathCompletion.split("c:/work/x"))
    }

    @Test
    fun split_treatsABareTildeAsHome() {
        assertEquals(PathCompletion.Query("~/", ""), PathCompletion.split("~"))
    }

    @Test
    fun split_offersNothingForTextThatCannotNameAnAbsoluteDirectoryYet() {
        assertNull(PathCompletion.split(""))
        assertNull(PathCompletion.split("code/proj"))
        assertNull(PathCompletion.split("C:"))
        assertNull(PathCompletion.split("~pavel"))
    }

    @Test
    fun rank_putsPrefixMatchesBeforeSubstringMatches_eachInTheAgentOrder() {
        assertEquals(
            listOf("code-b", "Code-a", "MyCode", "barcode"),
            PathCompletion.rank(listOf("MyCode", "code-b", "barcode", "Code-a", "docs"), "code"),
        )
    }

    @Test
    fun rank_hidesDotDirectoriesUntilTheFragmentStartsWithADot() {
        val dirs = listOf(".config", ".git", "config")

        assertEquals(listOf("config"), PathCompletion.rank(dirs, ""))
        assertEquals(listOf(".config", ".git"), PathCompletion.rank(dirs, "."))
        assertEquals(listOf(".config"), PathCompletion.rank(dirs, ".CON"))
    }

    @Test
    fun separator_isBackslashOnlyForAnAllBackslashPath() {
        assertEquals("\\", PathCompletion.separator("C:\\Users\\me\\"))
        assertEquals("/", PathCompletion.separator("C:/Users"))
        assertEquals("/", PathCompletion.separator("/home/me"))
        assertEquals("/", PathCompletion.separator(""))
    }

    @Test
    fun accept_appendsTheNameAndASeparatorInTheListedPathsOwnStyle() {
        assertEquals("/Users/pavel/", PathCompletion.accept("/Users", "pavel"))
        assertEquals("/Users/pavel/", PathCompletion.accept("/Users/", "pavel"))
        assertEquals("/Users/", PathCompletion.accept("/", "Users"))
        assertEquals("/home/me/code/", PathCompletion.accept("/home/me", "code"))
        assertEquals("C:\\Users\\me\\", PathCompletion.accept("C:\\Users", "me"))
        assertEquals("C:\\Users\\", PathCompletion.accept("C:\\", "Users"))
        assertEquals("C:/Users/me/", PathCompletion.accept("C:/Users", "me"))
    }

    @Test
    fun trimTrailingSeparator_dropsOneButKeepsRoots() {
        assertEquals("/a/b", PathCompletion.trimTrailingSeparator("/a/b/"))
        assertEquals("C:\\work", PathCompletion.trimTrailingSeparator("C:\\work\\"))
        assertEquals("/a/", PathCompletion.trimTrailingSeparator("/a//"))
        assertEquals("/a/b", PathCompletion.trimTrailingSeparator("/a/b"))
        assertEquals("/", PathCompletion.trimTrailingSeparator("/"))
        assertEquals("C:\\", PathCompletion.trimTrailingSeparator("C:\\"))
        assertEquals("C:/", PathCompletion.trimTrailingSeparator("C:/"))
    }

    @Test
    fun cache_answersEveryFragmentFromOneCompleteUnfilteredListing() {
        cache.store(ListingRequest("/srv/", null), listing("/srv", listOf("a", "b")))

        assertEquals(ListingHit(listing("/srv", listOf("a", "b")), complete = true), cache.lookup("/srv/", ""))
        assertEquals(true, cache.lookup("/srv/", "zz")?.complete)
    }

    @Test
    fun cache_marksATruncatedListingAsShownButNotComplete() {
        cache.store(ListingRequest("/big/", null), listing("/big", listOf("a"), truncated = true))

        assertEquals(ListingHit(listing("/big", listOf("a"), truncated = true), complete = false), cache.lookup("/big/", "a"))
    }

    @Test
    fun cache_letsAFilteredListingAnswerOnlyFragmentsContainingItsQ() {
        cache.store(ListingRequest("/big/", "AB"), listing("/big", listOf("abc", "xab")))

        assertEquals(true, cache.lookup("/big/", "xaby")?.complete)
        assertEquals(true, cache.lookup("/big/", "ab")?.complete)
        assertNull(cache.lookup("/big/", "a"))
    }

    @Test
    fun cache_neverAnswersADirectoryFromItsChild() {
        cache.store(ListingRequest("/a/b/", null), listing("/a/b", listOf("c")))

        assertNull(cache.lookup("/a/", ""))
        assertFalse(cache.hasUnfiltered("/a/"))
    }

    @Test
    fun cache_keepsAListingForTheTtlAndDropsItAfter() {
        cache.store(ListingRequest("/srv/", null), listing("/srv", listOf("a")))

        now += PathCompletion.LISTING_TTL_MS
        assertEquals(true, cache.lookup("/srv/", "")?.complete)
        assertTrue(cache.hasUnfiltered("/srv/"))

        now += 1
        assertFalse(cache.hasUnfiltered("/srv/"))
        assertNull(cache.lookup("/srv/", ""))
    }

    @Test
    fun cache_evictsTheLeastRecentlyUsedPast32_countingALookupAsAUse() {
        cache.store(ListingRequest("/kept/", null), listing("/kept", listOf("k")))
        for (i in 0 until 31) cache.store(ListingRequest("/d$i/", null), listing("/d$i", emptyList()))

        assertNotNull(cache.lookup("/kept/", ""))
        cache.store(ListingRequest("/new/", null), listing("/new", emptyList()))

        assertTrue(cache.hasUnfiltered("/kept/"))
        assertFalse(cache.hasUnfiltered("/d0/"))
        assertTrue(cache.hasUnfiltered("/d1/"))
    }

    @Test
    fun nextRequest_listsANewDirectoryUnfiltered() {
        assertEquals(ListingRequest("/srv/", null), cache.nextRequest(PathCompletion.Query("/srv/", "pro")))
    }

    @Test
    fun nextRequest_asksNothingMoreWhileTypingInsideACompleteListing() {
        cache.store(ListingRequest("/srv/", null), listing("/srv", listOf("proj")))

        assertNull(cache.nextRequest(PathCompletion.Query("/srv/", "")))
        assertNull(cache.nextRequest(PathCompletion.Query("/srv/", "pr")))
        assertNull(cache.nextRequest(PathCompletion.Query("/srv/", "proj")))
    }

    @Test
    fun nextRequest_reAsksATruncatedDirectoryWithTheFragmentAsQ_andOnlyWithOne() {
        cache.store(ListingRequest("/big/", null), listing("/big", listOf("a"), truncated = true))

        assertNull(cache.nextRequest(PathCompletion.Query("/big/", "")))
        assertEquals(ListingRequest("/big/", "ab"), cache.nextRequest(PathCompletion.Query("/big/", "ab")))

        cache.store(ListingRequest("/big/", "ab"), listing("/big", listOf("abc")))
        assertNull(cache.nextRequest(PathCompletion.Query("/big/", "abc")))
    }
}
