package com.mobaide.app.ui.terminal.git

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import com.mobaide.app.data.network.dto.GitHeadInfo
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusCounts
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The picker's menu is a popup Robolectric does not deliver clicks into, so
 * picking a branch is covered by the ViewModel and the pure `rebaseTargets`
 * tests instead.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class GitRebaseFormTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private fun status(counts: GitStatusCounts? = GitStatusCounts()) = GitStatusResponse(
        session_id = "s1",
        etag = "e1",
        repo = GitRepoInfo(
            toplevel = "/repo", git_dir = "/repo/.git", common_dir = "/repo/.git",
            head = GitHeadInfo(oid = "abc1234def", short_oid = "abc1234", branch = "topic"),
        ),
        counts = counts,
    )

    private fun setContent(
        state: RebaseSheetState = RebaseSheetState(),
        status: GitStatusResponse? = status(),
        onDismiss: () -> Unit = {},
        onRebase: (String) -> Unit = {},
    ) {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitRebaseForm(state = state, status = status, onDismiss = onDismiss, onRebase = onRebase)
            }
        }
    }

    private fun submit() = composeRule.onNodeWithTag(GIT_REBASE_SUBMIT_TAG)

    @Test
    fun aTypedRef_isSubmittedTrimmed_onceThereIsOne() {
        val sent = mutableListOf<String>()
        setContent(onRebase = { sent += it })

        composeRule.onNodeWithText("Rebase topic").assertIsDisplayed()
        submit().assertIsNotEnabled()

        composeRule.onNodeWithTag(GIT_REBASE_ONTO_FIELD_TAG).performTextInput("   ")
        submit().assertIsNotEnabled()

        composeRule.onNodeWithTag(GIT_REBASE_ONTO_FIELD_TAG).performTextInput("origin/main ")
        submit().assertIsEnabled().performClick()

        assertEquals(listOf("origin/main"), sent)
    }

    @Test
    fun thePicker_opensOnAPlaceholder_withNoPreselection() {
        setContent(
            state = RebaseSheetState(
                targets = RebaseTargetsState.Ready(listOf(GitItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "main"))),
            ),
        )

        composeRule.onNodeWithTag(GIT_REBASE_ONTO_PICKER_TAG).assertTextContains("Choose a branch")
        composeRule.onNodeWithTag(GIT_REBASE_ONTO_FIELD_TAG).assertDoesNotExist()
        submit().assertIsNotEnabled()
    }

    @Test
    fun aProjectWithNoOtherBranch_saysSo() {
        setContent(
            state = RebaseSheetState(
                targets = RebaseTargetsState.Ready(
                    listOf(GitItem(kind = GitItem.KIND_BRANCH_WITH_WORKTREE, branch = "topic")),
                ),
            ),
        )

        composeRule.onNodeWithTag(GIT_REBASE_ONTO_PICKER_TAG).assertTextContains("No other branches")
        submit().assertIsNotEnabled()
    }

    @Test
    fun whileTheBranchesLoad_nothingCanBeSubmitted() {
        setContent(state = RebaseSheetState(targets = RebaseTargetsState.Loading))

        composeRule.onNodeWithText("Loading branches…").assertIsDisplayed()
        submit().assertIsNotEnabled()
    }

    @Test
    fun trackedChanges_showTheBlocker_andKeepRebaseDisabled() {
        setContent(status = status(GitStatusCounts(unstaged = 1, total = 1)))

        composeRule.onNodeWithTag(GIT_REBASE_ONTO_FIELD_TAG).performTextInput("main")

        composeRule.onNodeWithTag(GIT_REBASE_BLOCKER_TAG)
            .assertIsDisplayed()
            .assertTextContains("Commit or discard your changes first.")
        submit().assertIsNotEnabled()
    }

    @Test
    fun untrackedFilesAlone_doNotBlock() {
        setContent(status = status(GitStatusCounts(untracked = 2, total = 2)))

        composeRule.onNodeWithTag(GIT_REBASE_ONTO_FIELD_TAG).performTextInput("main")

        composeRule.onNodeWithTag(GIT_REBASE_BLOCKER_TAG).assertDoesNotExist()
        submit().assertIsEnabled()
    }

    @Test
    fun aRefusal_showsItsCopy_andGitsOutput() {
        setContent(
            state = RebaseSheetState(
                error = CommitError(
                    "Local changes are in the way — stage, commit or discard them first.",
                    "error: cannot rebase: You have unstaged changes.",
                ),
            ),
        )

        composeRule.onNodeWithText("Local changes are in the way — stage, commit or discard them first.")
            .assertIsDisplayed()
        composeRule.onNodeWithText("error: cannot rebase: You have unstaged changes.").assertIsDisplayed()
    }

    @Test
    fun whileStarting_theSheetIsLocked() {
        var dismissed = 0
        setContent(state = RebaseSheetState(starting = true), onDismiss = { dismissed += 1 })

        submit().assertIsNotEnabled().assertTextContains("Rebasing…")
        composeRule.onNodeWithText("Cancel").assertIsNotEnabled()
        composeRule.onNodeWithTag(GIT_REBASE_ONTO_FIELD_TAG).assertIsNotEnabled()
        assertEquals(0, dismissed)
    }
}
