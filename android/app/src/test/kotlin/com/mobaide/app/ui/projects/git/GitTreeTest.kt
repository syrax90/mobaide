package com.mobaide.app.ui.projects.git

import com.mobaide.app.data.network.dto.GitItem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The project Git screen's folder tree and branch order: the same cases the
 * web and iOS clients pin, so the three render one list identically.
 */
class GitTreeTest {

    private fun branch(name: String): GitItem = GitItem(kind = GitItem.KIND_BRANCH_ONLY, branch = name)

    private fun remoteBranch(name: String, remote: String? = "origin"): GitItem =
        GitItem(kind = GitItem.KIND_REMOTE_BRANCH, branch = name, remote = remote)

    private fun orphan(path: String, name: String? = null): GitItem =
        GitItem(kind = GitItem.KIND_ORPHAN_WORKTREE, branch = name, worktree_path = path)

    /** One line per row, two spaces per depth: `▸ name (count)` / `▾ …` for folders, the label for rows. */
    private fun outline(rows: List<GitTreeRow>): List<String> = rows.map { row ->
        val indent = "  ".repeat(row.depth)
        when (row) {
            is GitTreeRow.Folder -> "$indent${if (row.expanded) "▾" else "▸"} ${row.name} (${row.count})"
            is GitTreeRow.Leaf -> "$indent${row.label}"
        }
    }

    // MARK: - Order

    @Test
    fun compareRefNames_sortsCaseInsensitively_withTheRawNameBreakingATie() {
        val names = listOf("main", "Feature/x", "feature/a", "develop", "fix/b", "alpha", "b", "B")

        assertEquals(
            listOf("alpha", "B", "b", "develop", "feature/a", "Feature/x", "fix/b", "main"),
            names.sortedWith(RefNameOrder),
        )
        assertEquals(0, compareRefNames("main", "main"))
    }

    @Test
    fun compareRefNames_comparesUtf16CodeUnits_notCodePointsOrLocaleOrder() {
        // U+1F600 is above U+FF5E as a code point, but its lead surrogate 0xD83D is below it.
        assertTrue(compareRefNames("😀", "～") < 0)
        assertEquals(listOf("1", "_x", "b"), listOf("b", "_x", "1").sortedWith(RefNameOrder))
    }

    @Test
    fun compareRefNames_keepsTwoSpellingsOfTheSameMarksInCodeUnitOrderOfTheirLowercasedNames() {
        // Canonically equivalent, but distinct refs to git: no normalisation may merge them.
        assertTrue(compareRefNames("ą́", "Ą́") < 0)
        assertTrue(compareRefNames("Ą́", "ą́") > 0)
    }

    // MARK: - Segments and ids

    @Test
    fun treeSegments_splitABranchOnItsSlashes_droppingEmptySegments() {
        assertEquals(listOf("feature", "login", "x"), treeSegments(branch("feature/login/x")))
        assertEquals(listOf("a", "b"), treeSegments(branch("a//b/")))
    }

    @Test
    fun treeSegments_fileARemoteRowUnderItsWholeRemoteName() {
        assertEquals(listOf("origin", "feature", "x"), treeSegments(remoteBranch("origin/feature/x")))
        assertEquals(listOf("up/stream", "main"), treeSegments(remoteBranch("up/stream/main", "up/stream")))
        assertEquals(listOf("origin", "main"), treeSegments(remoteBranch("origin/main", remote = null)))
    }

    @Test
    fun folderId_keysLocalFoldersOnTheirPath_andRemoteOnesOnTheRemoteThenThePathBelow() {
        assertEquals("dir:local:feature/login", folderId(GitTreeScope.LOCAL, listOf("feature", "login")))
        assertEquals("dir:remote:origin:", folderId(GitTreeScope.REMOTE, listOf("origin")))
        assertEquals(
            "dir:remote:origin:feature/x",
            folderId(GitTreeScope.REMOTE, listOf("origin", "feature", "x")),
        )
        assertNotEquals(
            folderId(GitTreeScope.REMOTE, listOf("a", "b")),
            folderId(GitTreeScope.REMOTE, listOf("a/b")),
        )
    }

    // MARK: - Rows

    @Test
    fun gitTreeRows_putCollapsedFoldersFirst_thenRows_eachLevelAToZIgnoringCase() {
        val items = listOf("main", "Feature/x", "feature/a", "develop", "fix/b", "alpha").map(::branch)

        assertEquals(
            listOf("▸ Feature (1)", "▸ feature (1)", "▸ fix (1)", "alpha", "develop", "main"),
            outline(gitTreeRows(items, GitTreeScope.LOCAL, emptySet(), filterActive = false)),
        )
    }

    @Test
    fun gitTreeRows_openOnlyTheToggledFolders_countingEveryRowBelow() {
        val items = listOf("feature/login/a", "feature/login/b", "feature/zeta", "feature/Alpha", "main")
            .map(::branch)

        assertEquals(
            listOf("▾ feature (4)", "  ▸ login (2)", "  Alpha", "  zeta", "main"),
            outline(gitTreeRows(items, GitTreeScope.LOCAL, setOf("dir:local:feature"), filterActive = false)),
        )

        val rows = gitTreeRows(
            items,
            GitTreeScope.LOCAL,
            setOf("dir:local:feature", "dir:local:feature/login"),
            filterActive = false,
        )
        assertEquals(
            listOf("▾ feature (4)", "  ▾ login (2)", "    a", "    b", "  Alpha", "  zeta", "main"),
            outline(rows),
        )
        assertEquals("dir:local:feature/login", (rows[1] as GitTreeRow.Folder).id)
        val leaf = rows[2] as GitTreeRow.Leaf
        assertEquals("feature/login/a", leaf.item.branch)
        assertEquals(2, leaf.depth)
    }

    @Test
    fun gitTreeRows_trailTheLocalTreeWithOrphanWorktreesByPath_whateverBranchTheyName() {
        val rows = gitTreeRows(
            listOf(orphan("/wt/b"), branch("main"), orphan("/wt/gone", "feature/gone"), orphan("/wt/A"), branch("z/x")),
            GitTreeScope.LOCAL,
            emptySet(),
            filterActive = false,
        )

        assertEquals(listOf("▸ z (1)", "main", "/wt/A", "/wt/b", "/wt/gone"), outline(rows))
        assertEquals(0, rows.last().depth)
    }

    @Test
    fun gitTreeRows_openRemoteNameFoldersByDefault_andKeepTheirSubFoldersCollapsed() {
        val rows = gitTreeRows(
            listOf(
                remoteBranch("origin/main"),
                remoteBranch("up/stream/main", "up/stream"),
                remoteBranch("origin/feature/x"),
            ),
            GitTreeScope.REMOTE,
            emptySet(),
            filterActive = false,
        )

        assertEquals(
            listOf("▾ origin (2)", "  ▸ feature (1)", "  main", "▾ up/stream (1)", "  main"),
            outline(rows),
        )
        assertEquals(
            listOf("dir:remote:origin:", "dir:remote:origin:feature", "dir:remote:up/stream:"),
            rows.filterIsInstance<GitTreeRow.Folder>().map { it.id },
        )
    }

    @Test
    fun gitTreeRows_toggleRemotesAAndABIndependently() {
        val items = listOf(remoteBranch("a/b/x", "a"), remoteBranch("a/b/y", "a/b"))

        assertEquals(
            listOf("▾ a (1)", "  ▸ b (1)", "▾ a/b (1)", "  y"),
            outline(gitTreeRows(items, GitTreeScope.REMOTE, emptySet(), filterActive = false)),
        )
        assertEquals(
            listOf("▾ a (1)", "  ▾ b (1)", "    x", "▾ a/b (1)", "  y"),
            outline(gitTreeRows(items, GitTreeScope.REMOTE, setOf("dir:remote:a:b"), filterActive = false)),
        )
        assertEquals(
            listOf("▾ a (1)", "  ▸ b (1)", "▸ a/b (1)"),
            outline(gitTreeRows(items, GitTreeScope.REMOTE, setOf("dir:remote:a/b:"), filterActive = false)),
        )
    }

    @Test
    fun gitTreeRows_openEveryFolderWhileAFilterIsActive_toggledOrNot() {
        assertEquals(
            listOf("▾ feature (1)", "  ▾ login (1)", "    a", "main"),
            outline(
                gitTreeRows(
                    listOf(branch("feature/login/a"), branch("main")),
                    GitTreeScope.LOCAL,
                    emptySet(),
                    filterActive = true,
                ),
            ),
        )
        assertEquals(
            listOf("▾ origin (1)", "  ▾ feature (1)", "    x"),
            outline(
                gitTreeRows(
                    listOf(remoteBranch("origin/feature/x")),
                    GitTreeScope.REMOTE,
                    setOf("dir:remote:origin:"),
                    filterActive = true,
                ),
            ),
        )
    }
}
