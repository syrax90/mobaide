package com.mobaide.app.ui.navigation

import android.net.Uri
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The Diff route builder. A repo-relative path travels as a query argument,
 * so every character that would otherwise split or terminate the query (`&`,
 * `=`, `#`, `+`, `/`, a space, a literal `%`) must be encoded exactly once —
 * navigation-compose parses the route with [Uri] and decodes once, which is
 * the oracle used here. Robolectric supplies the real [Uri] implementation.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RoutesGitDiffArgsTest {

    private val awkwardPaths = listOf(
        "notes/тест.md",
        "src/new name.py",
        "a%20b",
        "c#d",
        "a+b",
        "x&y=z",
    )

    private fun query(route: String): String = route.substringAfter('?')

    @Test
    fun awkwardPaths_roundTripThroughTheQuery() {
        for (path in awkwardPaths) {
            val route = Routes.sessionGitDiff("s1", path, "unstaged", origPath = null)
            val uri = Uri.parse(route)

            assertEquals(path, "session_git_diff/s1", route.substringBefore('?'))
            assertEquals(path, path, uri.getQueryParameter("path"))
            assertEquals(path, "unstaged", uri.getQueryParameter("scope"))
            assertEquals(path, setOf("path", "scope"), uri.queryParameterNames)

            val encodedPath = query(route).substringAfter("path=").substringBefore('&')
            for (raw in listOf(' ', '#', '&', '=', '+', '/')) {
                assertFalse("$path leaks a raw '$raw'", raw in encodedPath)
            }
        }
    }

    @Test
    fun aLiteralPercent_isEncodedRatherThanReinterpreted() {
        val route = Routes.sessionGitDiff("s1", "a%20b", "unstaged", origPath = null)
        assertTrue(route.contains("path=a%2520b"))
        assertEquals("a%20b", Uri.parse(route).getQueryParameter("path"))
    }

    @Test
    fun origPath_isAppendedOnlyWhenPresent() {
        val without = Routes.sessionGitDiff("s1", "src/a.py", "staged", origPath = null)
        assertFalse(without.contains("origPath"))
        assertNull(Uri.parse(without).getQueryParameter("origPath"))

        val with = Routes.sessionGitDiff("s1", "src/new name.py", "staged", origPath = "src/old #1.py")
        assertEquals("src/old #1.py", Uri.parse(with).getQueryParameter("origPath"))
        assertEquals("src/new name.py", Uri.parse(with).getQueryParameter("path"))
        assertEquals(setOf("path", "scope", "origPath"), Uri.parse(with).queryParameterNames)
    }

    @Test
    fun theWireShape_isPinned() {
        assertEquals(
            "session_git_diff/s1?path=src%2Fnew%20name.py&scope=staged&origPath=src%2Fold.py",
            Routes.sessionGitDiff("s1", "src/new name.py", "staged", "src/old.py"),
        )
        assertEquals(
            "session_git_diff/{sessionId}?path={path}&scope={scope}&origPath={origPath}",
            Routes.SESSION_GIT_DIFF,
        )
    }

    @Test
    fun theChangesRoute_matchesItsPattern() {
        assertEquals("session_git_changes/abc-123?commit=false", Routes.sessionGitChanges("abc-123"))
        assertEquals(
            Routes.SESSION_GIT_CHANGES.substringBefore('{'),
            Routes.sessionGitChanges("abc-123").substringBefore("abc-123"),
        )
    }

    @Test
    fun theChangesRoute_carriesTheCommitFlag_asAQueryArg() {
        // `commit=true` is the terminal menu's Commit…: the screen opens with
        // the sheet already up. Every caller writes the flag so the pattern's
        // `{commit}` placeholder is always filled.
        assertEquals("session_git_changes/abc-123?commit=false", Routes.sessionGitChanges("abc-123"))
        assertEquals(
            "session_git_changes/abc-123?commit=true",
            Routes.sessionGitChanges("abc-123", commit = true),
        )
        assertEquals("session_git_changes/{sessionId}?commit={commit}", Routes.SESSION_GIT_CHANGES)
        assertEquals(
            "true",
            Uri.parse(Routes.sessionGitChanges("abc-123", commit = true)).getQueryParameter("commit"),
        )
    }
}

/** The `sync` flag the terminal menu's *Fetch / Pull / Push…* sets on the Changes route. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RoutesGitChangesSyncArgTest {

    @Test
    fun theChangesRoute_carriesTheSyncFlag_asAQueryArg_besideCommit() {
        val route = Routes.sessionGitChanges("s1", sync = true)
        val uri = Uri.parse(route)

        assertEquals("session_git_changes/s1", route.substringBefore('?'))
        assertEquals("true", uri.getQueryParameter("sync"))
        assertEquals("false", uri.getQueryParameter("commit"))
        assertEquals(setOf("commit", "sync"), uri.queryParameterNames)
    }

    @Test
    fun theSyncFlag_defaultsToFalse_andThePatternNamesIt() {
        assertEquals("false", Uri.parse(Routes.sessionGitChanges("s1")).getQueryParameter("sync"))
        assertEquals("true", Uri.parse(Routes.sessionGitChanges("s1", commit = true)).getQueryParameter("commit"))
        assertEquals("session_git_changes/{sessionId}?commit={commit}&sync={sync}", Routes.SESSION_GIT_CHANGES)
    }
}
