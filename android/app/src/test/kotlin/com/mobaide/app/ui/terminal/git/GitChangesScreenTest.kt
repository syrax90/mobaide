package com.mobaide.app.ui.terminal.git

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import com.mobaide.app.data.network.dto.GitHeadInfo
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusCounts
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.GitUpstreamInfo
import com.mobaide.app.data.repository.GitCapabilities
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

    private val allCaps = GitCapabilities(stage = true, discard = true, commit = true)

    private fun ready(
        status: GitStatusResponse = status(),
        staleError: String? = null,
        capabilities: GitCapabilities = GitCapabilities.NONE,
        selectedKeys: Set<String> = emptySet(),
        busy: Boolean = false,
    ): GitChangesUiState.Ready =
        GitChangesUiState.Ready(
            status, status.repo!!, sectionsFor(status), staleError = staleError,
            capabilities = capabilities, selectedKeys = selectedKeys, busy = busy,
        )

    private fun setContent(
        state: GitChangesUiState,
        onEntryClick: (GitStatusEntry) -> Unit = {},
        onRetry: () -> Unit = {},
        onEntryLongClick: (GitStatusEntry) -> Unit = {},
        onStage: (List<GitStatusEntry>) -> Unit = {},
        onUnstage: (List<GitStatusEntry>) -> Unit = {},
    ) {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitChangesContent(
                    state = state,
                    onEntryClick = onEntryClick,
                    onRetry = onRetry,
                    onEntryLongClick = onEntryLongClick,
                    onStage = onStage,
                    onUnstage = onUnstage,
                )
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

    // MARK: - Stage / unstage controls

    @Test
    fun aReadOnlyAgent_showsNoStageControls() {
        setContent(ready())

        composeRule.onNodeWithTag(gitChangesStageTag(deleted)).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesUnstageTag(rename)).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesSectionActionTag(GitStatusEntry.GROUP_UNSTAGED)).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesSectionActionTag(GitStatusEntry.GROUP_STAGED)).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesSelectTag(deleted)).assertDoesNotExist()
    }

    @Test
    fun rows_offerStageOrUnstage_bySection_andAConflictNothing() {
        val staged = mutableListOf<List<GitStatusEntry>>()
        val unstaged = mutableListOf<List<GitStatusEntry>>()
        setContent(ready(capabilities = allCaps), onStage = { staged += it }, onUnstage = { unstaged += it })

        composeRule.onNodeWithTag(gitChangesStageTag(deleted)).assertIsDisplayed().performClick()
        composeRule.onNodeWithTag(gitChangesStageTag(untracked)).assertIsDisplayed()
        composeRule.onNodeWithTag(gitChangesUnstageTag(rename)).assertIsDisplayed().performClick()
        composeRule.onNodeWithTag(gitChangesUnstageTag(deleted)).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesStageTag(rename)).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesStageTag(conflict)).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesUnstageTag(conflict)).assertDoesNotExist()

        assertEquals(listOf(listOf(deleted)), staged)
        assertEquals(listOf(listOf(rename)), unstaged)
    }

    @Test
    fun sectionHeaders_stageOrUnstageTheirWholeSection() {
        val staged = mutableListOf<List<GitStatusEntry>>()
        val unstaged = mutableListOf<List<GitStatusEntry>>()
        setContent(ready(capabilities = allCaps), onStage = { staged += it }, onUnstage = { unstaged += it })

        composeRule.onNodeWithTag(gitChangesSectionActionTag(GitStatusEntry.GROUP_CONFLICTS)).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesSectionActionTag(GitStatusEntry.GROUP_STAGED))
            .assertIsDisplayed()
            .performClick()
        composeRule.onNodeWithTag(gitChangesSectionActionTag(GitStatusEntry.GROUP_UNSTAGED))
            .assertIsDisplayed()
            .performClick()
        composeRule.onNodeWithTag(gitChangesSectionActionTag(GitStatusEntry.GROUP_UNTRACKED))
            .assertIsDisplayed()
            .performClick()

        assertEquals(listOf(listOf(deleted), listOf(untracked)), staged)
        assertEquals(listOf(listOf(rename, submodule)), unstaged)
    }

    @Test
    fun longPressingARow_reportsItsEntry() {
        val pressed = mutableListOf<GitStatusEntry>()
        setContent(ready(capabilities = allCaps), onEntryLongClick = { pressed += it })

        composeRule.onNodeWithTag(gitChangesRowTag(deleted)).performTouchInput { longClick() }

        assertEquals(listOf(deleted), pressed)
    }

    @Test
    fun selectionMode_showsCheckboxes_andHidesTheRowAndSectionActions() {
        val clicked = mutableListOf<GitStatusEntry>()
        val state = ready(capabilities = allCaps, selectedKeys = setOf("unstaged:1:src/gone.py"))
        setContent(state, onEntryClick = { clicked += it })

        composeRule.onNodeWithTag(gitChangesSelectTag(deleted)).assertIsDisplayed().assertIsOn()
        composeRule.onNodeWithTag(gitChangesSelectTag(untracked)).assertIsDisplayed()
        composeRule.onNodeWithTag(gitChangesSelectTag(conflict)).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesStageTag(deleted)).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesUnstageTag(rename)).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesSectionActionTag(GitStatusEntry.GROUP_UNSTAGED)).assertDoesNotExist()

        composeRule.onNodeWithTag(gitChangesSelectTag(untracked)).performClick()

        assertEquals(listOf(untracked), clicked)
    }

    @Test
    fun whileAMutationIsInFlight_everyControlWaits() {
        setContent(ready(capabilities = allCaps, busy = true))

        composeRule.onNodeWithTag(gitChangesStageTag(deleted)).assertIsNotEnabled()
        composeRule.onNodeWithTag(gitChangesUnstageTag(rename)).assertIsNotEnabled()
        composeRule.onNodeWithTag(gitChangesSectionActionTag(GitStatusEntry.GROUP_STAGED)).assertIsNotEnabled()
    }

    // MARK: - Discard confirmation

    @Test
    fun discardDialog_namesTheFile_andOnlyConfirmDiscards() {
        var confirmed = 0
        var dismissed = 0
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitDiscardDialog(
                    prompt = DiscardPrompt(listOf(untracked)),
                    busy = false,
                    onConfirm = { confirmed++ },
                    onDismiss = { dismissed++ },
                )
            }
        }

        composeRule.onNodeWithTag(GIT_CHANGES_DISCARD_DIALOG_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Delete untracked file?").assertIsDisplayed()
        composeRule.onNodeWithText(
            "notes/тест.md is not tracked by git and will be deleted. This cannot be undone.",
        ).assertIsDisplayed()

        composeRule.onNodeWithText("Cancel").performClick()
        assertEquals(0 to 1, confirmed to dismissed)

        composeRule.onNodeWithText("Discard").assertIsEnabled().performClick()
        assertEquals(1 to 1, confirmed to dismissed)
    }

    @Test
    fun discardDialog_locksConfirmWhileBusy() {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitDiscardDialog(
                    prompt = DiscardPrompt(listOf(deleted, untracked)),
                    busy = true,
                    onConfirm = {},
                    onDismiss = {},
                )
            }
        }

        composeRule.onNodeWithText("Discard 2 files?").assertIsDisplayed()
        composeRule.onNodeWithText("Discard").assertIsNotEnabled()
    }
}
