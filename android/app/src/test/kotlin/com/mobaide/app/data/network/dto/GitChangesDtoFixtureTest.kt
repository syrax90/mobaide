package com.mobaide.app.data.network.dto

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of `tests/protocol_fixtures/dto/agent/GitStatusResponse.json`
 * and `GitDiffResponse.json`. Every case carries the agent's `wire` body (each
 * key present, nulls included) and the `decoded_canonical` shape with the
 * nulls dropped; the DTOs' declared defaults are what let the second one
 * decode, so both must reach the same value under the `RestApi` decoder
 * configuration.
 */
class GitChangesDtoFixtureTest {

    @Serializable
    private data class Case(
        val name: String,
        val wire: JsonObject,
        val decoded_canonical: JsonObject,
    )

    @Serializable
    private data class Fixture(val type: String, val cases: List<Case>)

    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private val statusFixture: Fixture = ProtocolFixtures.load("dto/agent/GitStatusResponse.json")
    private val diffFixture: Fixture = ProtocolFixtures.load("dto/agent/GitDiffResponse.json")

    private fun status(name: String): GitStatusResponse =
        json.decodeFromJsonElement(
            GitStatusResponse.serializer(),
            statusFixture.cases.first { it.name == name }.wire,
        )

    private fun diff(name: String): GitDiffResponse =
        json.decodeFromJsonElement(
            GitDiffResponse.serializer(),
            diffFixture.cases.first { it.name == name }.wire,
        )

    @Test
    fun fixtures_haveCasesToRun() {
        assertEquals("GitStatusResponse", statusFixture.type)
        assertEquals("GitDiffResponse", diffFixture.type)
        assertTrue("status cases", statusFixture.cases.isNotEmpty())
        assertTrue("diff cases", diffFixture.cases.isNotEmpty())
    }

    @Test
    fun status_wireAndCanonical_decodeToTheSameValue() {
        for (case in statusFixture.cases) {
            val fromWire = json.decodeFromJsonElement(GitStatusResponse.serializer(), case.wire)
            val fromCanonical =
                json.decodeFromJsonElement(GitStatusResponse.serializer(), case.decoded_canonical)
            assertEquals(case.name, fromWire, fromCanonical)
        }
    }

    @Test
    fun diff_wireAndCanonical_decodeToTheSameValue() {
        for (case in diffFixture.cases) {
            val fromWire = json.decodeFromJsonElement(GitDiffResponse.serializer(), case.wire)
            val fromCanonical =
                json.decodeFromJsonElement(GitDiffResponse.serializer(), case.decoded_canonical)
            assertEquals(case.name, fromWire, fromCanonical)
        }
    }

    @Test
    fun linkedWorktreeFull_decodesRepoUpstreamAndEveryEntryShape() {
        val status = status("linked_worktree_full")
        val repo = status.repo!!
        assertTrue(repo.is_linked_worktree)
        assertEquals("/home/user/proj/.wt/feature-x", repo.toplevel)
        assertEquals("/home/user/proj/.git", repo.common_dir)
        assertEquals("feature/x", repo.head.branch)
        assertEquals(GitRepoInfo.STATE_MERGING, repo.state)
        assertEquals(2, repo.upstream?.ahead)
        assertEquals(0, repo.upstream?.behind)
        assertEquals(6, status.entries.size)
        assertEquals(6, status.counts?.total)
        assertNull(status.unavailable_reason)

        val rename = status.entries.first { it.code == "R" }
        assertEquals(GitStatusEntry.GROUP_STAGED, rename.group)
        assertEquals("src/new name.py", rename.path)
        assertEquals("src/old.py", rename.orig_path)
        assertEquals(90, rename.rename_score)

        assertTrue(status.entries.first { it.path == "vendor/lib" }.submodule)

        val untracked = status.entries.first { it.group == GitStatusEntry.GROUP_UNTRACKED }
        assertEquals("notes/тест.md", untracked.path)
        assertEquals("тест.md", untracked.fileName)
        assertEquals("notes", untracked.directory)

        val conflict = status.entries.first { it.group == GitStatusEntry.GROUP_CONFLICTS }
        assertEquals("UU", conflict.code)
        assertNull(conflict.orig_path)
        assertNull(conflict.rename_score)
        assertFalse(conflict.submodule)
    }

    @Test
    fun rebasingStoppedOnConflict_readsTheRebaseProgress_andOtherCasesCarryNone() {
        val repo = status("rebasing_stopped_on_conflict").repo!!

        assertEquals(GitRepoInfo.STATE_REBASING, repo.state)
        assertTrue(repo.head.detached)
        assertEquals("topic", repo.rebase?.branch)
        assertEquals("origin/main", repo.rebase?.onto_ref)
        assertEquals("89abcde", repo.rebase?.onto_short_oid)
        assertEquals(GitRebaseCommit("fedcba9876543210fedcba9876543210fedcba98", "fedcba9", "Fix the login redirect"), repo.rebase?.stopped_on)
        assertEquals(2, repo.rebase?.step)
        assertEquals(5, repo.rebase?.total)
        assertNull(status("linked_worktree_full").repo?.rebase)
    }

    @Test
    fun detachedHead_hasNoBranchAndNoUpstream() {
        val repo = status("detached_head").repo!!
        assertTrue(repo.head.detached)
        assertNull(repo.head.branch)
        assertEquals("0123456", repo.head.short_oid)
        assertNull(repo.upstream)
        assertEquals(GitRepoInfo.STATE_CLEAN, repo.state)
    }

    @Test
    fun initialRepo_hasABranchButNoCommit() {
        val status = status("initial_repo_no_upstream")
        val repo = status.repo!!
        assertTrue(repo.head.initial)
        assertNull(repo.head.oid)
        assertNull(repo.head.short_oid)
        assertEquals("main", repo.head.branch)
        assertNull(repo.upstream)
        val readme = status.entries.single()
        assertEquals("README.md", readme.fileName)
        assertNull(readme.directory)
    }

    @Test
    fun upstreamGone_keepsTheRefWithNullCounts() {
        val repo = status("upstream_gone").repo!!
        assertEquals("origin/topic", repo.upstream?.ref)
        assertNull(repo.upstream?.ahead)
        assertNull(repo.upstream?.behind)
        assertEquals(GitRepoInfo.STATE_REBASING, repo.state)
    }

    @Test
    fun unchanged_carriesOnlyTheEtag() {
        val status = status("unchanged")
        assertTrue(status.unchanged)
        assertNull(status.repo)
        assertNull(status.counts)
        assertNull(status.unavailable_reason)
        assertTrue(status.entries.isEmpty())
        assertEquals(status("linked_worktree_full").etag, status.etag)
    }

    @Test
    fun notARepo_reportsTheReasonWithoutARepo() {
        val status = status("not_a_repo")
        assertEquals(GitStatusResponse.UNAVAILABLE_NOT_A_REPO, status.unavailable_reason)
        assertNull(status.repo)
        assertFalse(status.unchanged)
        assertEquals("/home/user/plain", status.cwd)
    }

    @Test
    fun truncatedWindows_flagsTruncationWithTheFullCount() {
        val status = status("truncated_windows")
        assertTrue(status.truncated)
        assertEquals(5000, status.entry_limit)
        assertEquals(6001, status.counts?.total)
        assertEquals(2, status.entries.size)
        assertEquals("C:/Users/dev/proj", status.repo?.toplevel)
        assertEquals(3, status.repo?.upstream?.behind)
    }

    @Test
    fun textModified_keepsTheCarriageReturnAndLineNumbers() {
        val diff = diff("text_modified")
        assertEquals(GitDiffResponse.KIND_TEXT, diff.kind)
        assertEquals(GitDiffResponse.CHANGE_MODIFIED, diff.change)
        assertEquals(GitDiffResponse.SCOPE_UNSTAGED, diff.scope)
        assertEquals(2, diff.additions)
        assertEquals(1, diff.deletions)
        assertNull(diff.orig_path)

        val hunk = diff.hunks.single()
        assertEquals("class Foo:", hunk.heading)
        assertEquals(6, hunk.old_start)
        assertEquals(4, hunk.old_lines)
        assertEquals(6, hunk.new_start)
        assertEquals(5, hunk.new_lines)

        val lines = hunk.lines
        assertEquals(6, lines.size)
        assertEquals("    x = 1\r", lines[1].text)
        assertEquals(GitDiffLine.T_DEL, lines[2].t)
        assertEquals(8, lines[2].old)
        assertNull(lines[2].new)
        assertEquals(GitDiffLine.T_ADD, lines[3].t)
        assertNull(lines[3].old)
        assertEquals(8, lines[3].new)
        assertEquals("", lines[5].text)
        assertTrue(lines.none { it.no_newline })
    }

    @Test
    fun renamed_carriesTheOriginalPath() {
        val diff = diff("renamed_with_heading")
        assertEquals("src/new name.py", diff.path)
        assertEquals("src/old.py", diff.orig_path)
        assertEquals(GitDiffResponse.CHANGE_RENAMED, diff.change)
        assertEquals(GitDiffResponse.SCOPE_STAGED, diff.scope)
        assertEquals("", diff.hunks.single().heading)
    }

    @Test
    fun noNewlineAdded_marksOnlyTheLastLine() {
        val diff = diff("no_newline_added")
        assertEquals(GitDiffResponse.CHANGE_ADDED, diff.change)
        assertNull(diff.old_mode)
        assertEquals("100644", diff.new_mode)
        val lines = diff.hunks.single().lines
        assertEquals(2, lines.size)
        assertFalse(lines.first().no_newline)
        assertTrue(lines.last().no_newline)
        assertEquals("last", lines.last().text)
    }

    @Test
    fun typeChanged_hasTwoHunksAcrossTheModeSwap() {
        val diff = diff("type_changed")
        assertEquals(GitDiffResponse.CHANGE_TYPE_CHANGED, diff.change)
        assertEquals("120000", diff.old_mode)
        assertEquals("100644", diff.new_mode)
        assertEquals(2, diff.hunks.size)
        assertTrue(diff.hunks[0].lines.single().no_newline)
        assertEquals(GitDiffLine.T_ADD, diff.hunks[1].lines.single().t)
    }

    @Test
    fun binaryAndMissing_haveNoHunks() {
        val binary = diff("binary")
        assertEquals(GitDiffResponse.KIND_BINARY, binary.kind)
        assertEquals(GitDiffResponse.CHANGE_MODIFIED, binary.change)
        assertTrue(binary.hunks.isEmpty())

        val missing = diff("missing")
        assertEquals(GitDiffResponse.KIND_MISSING, missing.kind)
        assertNull(missing.change)
        assertNull(missing.old_mode)
        assertNull(missing.new_mode)
        assertTrue(missing.hunks.isEmpty())
    }

    @Test
    fun submodule_diffsTheCommitPointers() {
        val diff = diff("submodule")
        assertEquals(GitDiffResponse.KIND_SUBMODULE, diff.kind)
        assertEquals("160000", diff.old_mode)
        assertEquals("160000", diff.new_mode)
        val lines = diff.hunks.single().lines
        assertEquals(listOf(GitDiffLine.T_DEL, GitDiffLine.T_ADD), lines.map { it.t })
        assertTrue(lines.all { it.text.startsWith("Subproject commit ") })
    }

    @Test
    fun truncated_reportsTheTotalBeyondTheLimit() {
        val diff = diff("truncated")
        assertTrue(diff.truncated)
        assertEquals(12345, diff.total_lines)
        assertEquals(5000, diff.line_limit)
        assertEquals(1_048_576, diff.byte_limit)
        assertEquals(9000, diff.additions)
        assertEquals(1, diff.hunks.single().lines.size)
    }
}
