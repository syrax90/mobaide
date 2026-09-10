package com.mobaide.app.ui.terminal.git

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.mobaide.app.data.network.dto.GitHeadInfo
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusCounts
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.GitUpstreamInfo
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Renders [GitChangesContent] in every [GitChangesUiState] and checks what the
 * user can see and tap. The section titles and chips go through
 * `chromeCaps()`, so they are matched uppercase.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class GitChangesScreenTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val conflict = GitStatusEntry(
        group = GitStatusEntry.GROUP_CONFLICTS, path = "src/conflict.py",
        code = "UU", index_status = "U", worktree_status = "U",
    )
    private val rename = GitStatusEntry(
        group = GitStatusEntry.GROUP_STAGED, path = "src/new name.py", orig_path = "src/old.py",
        code = "R", index_status = "R", worktree_status = ".", rename_score = 90,
    )
    private val submodule = GitStatusEntry(
        group = GitStatusEntry.GROUP_STAGED, path = "vendor/lib",
        code = "M", index_status = "M", worktree_status = ".", submodule = true,
    )
    private val deleted = GitStatusEntry(
        group = GitStatusEntry.GROUP_UNSTAGED, path = "src/gone.py",
        code = "D", index_status = ".", worktree_status = "D",
    )
    private val untracked = GitStatusEntry(
        group = GitStatusEntry.GROUP_UNTRACKED, path = "notes/тест.md",
        code = "?", index_status = "?", worktree_status = "?",
    )

    private fun status(
        entries: List<GitStatusEntry> = listOf(conflict, rename, submodule, deleted, untracked),
        truncated: Boolean = false,
    ): GitStatusResponse = GitStatusResponse(
        session_id = "s1",
        cwd = "/home/user/proj/.wt/feature-x",
        etag = "e1",
        repo = GitRepoInfo(
            toplevel = "/home/user/proj/.wt/feature-x",
            git_dir = "/home/user/proj/.git/worktrees/feature-x",
            common_dir = "/home/user/proj/.git",
            is_linked_worktree = true,
            head = GitHeadInfo(oid = "0123456789abcdef", short_oid = "0123456", branch = "feature/x"),
            upstream = GitUpstreamInfo(ref = "origin/feature/x", ahead = 2, behind = 0),
            state = GitRepoInfo.STATE_MERGING,
        ),
        entries = entries,
        counts = GitStatusCounts(total = if (truncated) 6001 else entries.size),
        truncated = truncated,
    )

    private fun ready(
        status: GitStatusResponse = status(),
        staleError: String? = null,
    ): GitChangesUiState.Ready =
        GitChangesUiState.Ready(status, status.repo!!, sectionsFor(status), staleError = staleError)

    private fun setContent(
        state: GitChangesUiState,
        onEntryClick: (GitStatusEntry) -> Unit = {},
        onRetry: () -> Unit = {},
    ) {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitChangesContent(state = state, onEntryClick = onEntryClick, onRetry = onRetry)
            }
        }
    }

    @Test
    fun ready_rendersTheRepoHeader() {
        setContent(ready())

        composeRule.onNodeWithText("feature/x").assertIsDisplayed()
        composeRule.onNodeWithText("↑2 ↓0 · origin/feature/x").assertIsDisplayed()
        composeRule.onNodeWithText("MERGING").assertIsDisplayed()
        composeRule.onNodeWithText("WORKTREE").assertIsDisplayed()
        composeRule.onNodeWithText("/home/user/proj/.wt/feature-x").assertIsDisplayed()
    }

    @Test
    fun ready_rendersEverySectionInOrder_withItsRows() {
        setContent(ready())

        composeRule.onNodeWithText("CONFLICTS").assertIsDisplayed()
        composeRule.onNodeWithText("STAGED").assertIsDisplayed()
        composeRule.onNodeWithText("CHANGES").assertIsDisplayed()
        composeRule.onNodeWithText("UNTRACKED").assertIsDisplayed()

        composeRule.onNodeWithText("conflict.py").assertIsDisplayed()
        composeRule.onNodeWithText("new name.py").assertIsDisplayed()
        composeRule.onNodeWithText("lib").assertIsDisplayed()
        composeRule.onNodeWithText("gone.py").assertIsDisplayed()
        composeRule.onNodeWithText("тест.md").assertIsDisplayed()

        composeRule.onNodeWithTag(gitChangesRowTag(conflict)).assertIsDisplayed()
        composeRule.onNodeWithTag(gitChangesRowTag(untracked)).assertIsDisplayed()
    }

    @Test
    fun rows_showTheBadgeLetter_theRenameArrow_andTheSubmoduleChip() {
        setContent(ready())

        composeRule.onNodeWithText("UU").assertIsDisplayed()
        composeRule.onNodeWithText("R").assertIsDisplayed()
        composeRule.onNodeWithText("M").assertIsDisplayed()
        composeRule.onNodeWithText("D").assertIsDisplayed()
        composeRule.onNodeWithText("?").assertIsDisplayed()

        composeRule.onNodeWithText("src/old.py → src/new name.py").assertIsDisplayed()
        composeRule.onNodeWithText("SUBMODULE").assertIsDisplayed()
        composeRule.onNodeWithText("notes").assertIsDisplayed()
        composeRule.onNodeWithText("vendor").assertIsDisplayed()
    }

    @Test
    fun tappingARow_reportsItsEntry() {
        val clicked = mutableListOf<GitStatusEntry>()
        setContent(ready(), onEntryClick = { clicked += it })

        composeRule.onNodeWithTag(gitChangesRowTag(rename)).performClick()

        assertEquals(1, clicked.size)
        assertSame(rename, clicked.single())
    }

    @Test
    fun twoRowsSharingAPath_bothRender() {
        // The agent's lossy filename decoding can hand two entries the same
        // path; LazyColumn needs a distinct key for each row.
        val twin = untracked.copy(path = "notes/�.md")
        setContent(ready(status(entries = listOf(twin, twin))))

        composeRule.onAllNodesWithTag(gitChangesRowTag(twin)).assertCountEquals(2)
    }

    @Test
    fun aCleanTree_showsTheEmptyState_andNoSections() {
        setContent(ready(status(entries = emptyList())))

        composeRule.onNodeWithTag(GIT_CHANGES_EMPTY_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Working tree clean").assertIsDisplayed()
        composeRule.onNodeWithText("STAGED").assertDoesNotExist()
        composeRule.onNodeWithText("feature/x").assertIsDisplayed()
    }

    @Test
    fun aTruncatedList_saysHowMuchIsMissing() {
        setContent(ready(status(entries = listOf(untracked), truncated = true)))

        composeRule.onNodeWithText("Showing the first 1 of at least 6001 entries.").assertIsDisplayed()
    }

    @Test
    fun aStaleError_bannersAboveTheList_andRetries() {
        var retries = 0
        setContent(
            ready(staleError = "Could not reach the agent."),
            onRetry = { retries++ },
        )

        composeRule.onNodeWithTag(GIT_CHANGES_STALE_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Could not reach the agent.").assertIsDisplayed()
        composeRule.onNodeWithText("conflict.py").assertIsDisplayed()

        composeRule.onNodeWithText("Retry").performClick()
        assertEquals(1, retries)
    }

    @Test
    fun loading_showsTheCursor() {
        setContent(GitChangesUiState.Loading)
        composeRule.onNodeWithTag(GIT_CHANGES_LOADING_TAG).assertIsDisplayed()
    }

    @Test
    fun unavailable_namesTheReasonAndTheFolder() {
        setContent(
            GitChangesUiState.Unavailable(GitStatusResponse.UNAVAILABLE_NOT_A_REPO, "/home/user/plain"),
        )

        composeRule.onNodeWithTag(GIT_CHANGES_UNAVAILABLE_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Not a git repository").assertIsDisplayed()
        composeRule.onNodeWithText("/home/user/plain").assertIsDisplayed()
        composeRule.onNodeWithText("Retry").assertDoesNotExist()
    }

    @Test
    fun agentOutdated_asksForAnUpdate() {
        setContent(GitChangesUiState.AgentOutdated)

        composeRule.onNodeWithTag(GIT_CHANGES_OUTDATED_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Update your agent").assertIsDisplayed()
    }

    @Test
    fun sessionGone_saysTheSessionEnded() {
        setContent(GitChangesUiState.SessionGone)

        composeRule.onNodeWithTag(GIT_CHANGES_SESSION_GONE_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Session ended").assertIsDisplayed()
    }

    @Test
    fun error_showsTheMessage_andRetryCallsBack() {
        var retries = 0
        setContent(GitChangesUiState.Error("git failed: boom"), onRetry = { retries++ })

        composeRule.onNodeWithTag(GIT_CHANGES_ERROR_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("git failed: boom").assertIsDisplayed()

        composeRule.onNodeWithText("Retry").assertIsDisplayed().performClick()
        assertEquals(1, retries)
    }
}
