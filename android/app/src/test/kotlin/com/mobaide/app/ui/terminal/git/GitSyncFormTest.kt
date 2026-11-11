package com.mobaide.app.ui.terminal.git

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.mobaide.app.data.network.dto.GitHeadInfo
import com.mobaide.app.data.network.dto.GitJobResponse
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitUpstreamInfo
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Renders [GitSyncForm] (the sheet's content, without the sheet window) and
 * the follow-up confirmation, and checks which rows the user can tap, what a
 * running or finished job shows, and which callbacks each tap reaches.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class GitSyncFormTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val syncCaps = GitCapabilities(fetch = true, pull = true, push = true)

    private fun repo(
        upstream: GitUpstreamInfo? = GitUpstreamInfo("origin/main", ahead = 1, behind = 2),
    ) = GitRepoInfo(
        toplevel = "/repo", git_dir = "/repo/.git", common_dir = "/repo/.git",
        head = GitHeadInfo(oid = "abc1234def", short_oid = "abc1234", branch = "main"),
        upstream = upstream,
    )

    private fun job(
        op: String = GitJobResponse.OP_PUSH,
        status: String = GitJobResponse.STATUS_RUNNING,
        phase: String? = null,
        percent: Int? = null,
        line: String? = null,
        ok: Boolean? = null,
        errorCode: String? = null,
        message: String? = null,
    ) = GitJobResponse(
        job_id = "j1", session_id = "s1", op = op, status = status,
        phase = phase, percent = percent, line = line,
        ok = ok, error_code = errorCode, message = message,
    )

    private fun setContent(
        state: SyncSheetState = SyncSheetState(),
        repo: GitRepoInfo? = repo(),
        capabilities: GitCapabilities = syncCaps,
        banner: GitJobBanner? = null,
        onDismiss: () -> Unit = {},
        onStart: (String) -> Unit = {},
        onCancelJob: () -> Unit = {},
        onFollowUp: (SyncFollowUp) -> Unit = {},
    ) {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitSyncForm(
                    state = state,
                    repo = repo,
                    capabilities = capabilities,
                    banner = banner,
                    onDismiss = onDismiss,
                    onStart = onStart,
                    onCancelJob = onCancelJob,
                    onFollowUp = onFollowUp,
                )
            }
        }
    }

    private fun row(op: String) = composeRule.onNodeWithTag(gitSyncRowTag(op))

    @Test
    fun rows_showTheHeadAndUpstream_startTheirOp_andCarryTheirReason() {
        val starts = mutableListOf<String>()
        setContent(onStart = { starts += it })

        composeRule.onNodeWithText("main").assertIsDisplayed()
        composeRule.onNodeWithText("↑1 ↓2 · origin/main").assertIsDisplayed()
        composeRule.onNodeWithText("Update remote-tracking branches").assertIsDisplayed()
        composeRule.onNodeWithText("Fast-forward 2 commits from origin/main").assertIsDisplayed()
        composeRule.onNodeWithText("1 commit to origin/main").assertIsDisplayed()

        row("fetch").assertIsEnabled().performClick()
        row("pull").assertIsEnabled().performClick()
        row("push").assertIsEnabled().performClick()

        assertEquals(listOf("fetch", "pull", "push"), starts)
    }

    @Test
    fun rowsWithNothingToDo_areDisabled_withTheirReason() {
        setContent(repo = repo(GitUpstreamInfo("origin/main", ahead = 0, behind = 0)))

        row("fetch").assertIsEnabled()
        row("pull").assertIsNotEnabled()
        composeRule.onNodeWithText("Nothing to pull").assertIsDisplayed()
        row("push").assertIsNotEnabled()
        composeRule.onNodeWithText("Nothing to push").assertIsDisplayed()
    }

    @Test
    fun whileStarting_everyRowAndCloseLock() {
        setContent(state = SyncSheetState(starting = true))

        row("fetch").assertIsNotEnabled()
        row("pull").assertIsNotEnabled()
        row("push").assertIsNotEnabled()
        composeRule.onNodeWithText("Close").assertIsNotEnabled()
    }

    @Test
    fun aRunningJob_showsItsProgress_locksTheRows_keepsClose_andCancels() {
        var cancels = 0
        setContent(
            banner = GitJobBanner(
                "j1",
                job(phase = "Writing objects", percent = 45, line = "Writing objects:  45% (14/32)"),
            ),
            onCancelJob = { cancels += 1 },
        )

        composeRule.onNodeWithTag(GIT_SYNC_PROGRESS_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Pushing…").assertIsDisplayed()
        composeRule.onNodeWithText("Writing objects 45%").assertIsDisplayed()
        composeRule.onNodeWithText("Writing objects:  45% (14/32)").assertIsDisplayed()
        composeRule.onNodeWithTag(GIT_SYNC_RESULT_TAG).assertDoesNotExist()
        row("fetch").assertIsNotEnabled()
        row("push").assertIsNotEnabled()
        composeRule.onNodeWithText("Close").assertIsEnabled()

        composeRule.onNodeWithTag(GIT_SYNC_CANCEL_TAG).performClick()

        assertEquals(1, cancels)
    }

    @Test
    fun aFailedPush_reportsTheCopyAndGitsOutput_andOffersTheLeaseFollowUp() {
        val followUps = mutableListOf<SyncFollowUp>()
        setContent(
            banner = GitJobBanner(
                "j1",
                job(
                    status = GitJobResponse.STATUS_DONE, ok = false, errorCode = "rejected",
                    message = "! [rejected] main -> main (fetch first)",
                ),
            ),
            onFollowUp = { followUps += it },
        )

        composeRule.onNodeWithTag(GIT_SYNC_RESULT_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Push failed").assertIsDisplayed()
        composeRule.onNodeWithText("The remote rejected the push.").assertIsDisplayed()
        composeRule.onNodeWithText("! [rejected] main -> main (fetch first)").assertIsDisplayed()
        composeRule.onNodeWithTag(gitSyncFollowUpTag(SyncFollowUp.PullRebase)).assertDoesNotExist()
        row("fetch").assertIsEnabled()

        composeRule.onNodeWithTag(gitSyncFollowUpTag(SyncFollowUp.ForcePush)).assertIsEnabled().performClick()

        assertEquals(listOf(SyncFollowUp.ForcePush), followUps)
    }

    @Test
    fun theConfirmDialog_confirmsOrDismisses() {
        var confirms = 0
        var dismissals = 0
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitSyncConfirmDialog(
                    prompt = followUpPrompt(SyncFollowUp.ForcePush, repo()),
                    destructive = true,
                    onConfirm = { confirms += 1 },
                    onDismiss = { dismissals += 1 },
                )
            }
        }

        composeRule.onNodeWithText("Force push?").assertIsDisplayed()
        composeRule.onNodeWithText(
            "Overwrites origin/main with your branch. Proceeds only if the remote hasn't moved since your last fetch.",
        ).assertIsDisplayed()
        composeRule.onNodeWithText("Force push").performClick()
        composeRule.onNodeWithText("Cancel").performClick()

        assertEquals(1, confirms)
        assertEquals(1, dismissals)
    }
}
