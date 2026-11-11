package com.mobaide.app.ui.terminal.git

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertHasNoClickAction
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.mobaide.app.data.network.dto.GitHeadInfo
import com.mobaide.app.data.network.dto.GitJobResponse
import com.mobaide.app.data.network.dto.GitRebaseCommit
import com.mobaide.app.data.network.dto.GitRebaseInfo
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
        job: GitJobBanner? = null,
    ): GitChangesUiState.Ready =
        GitChangesUiState.Ready(
            status, status.repo!!, sectionsFor(status), staleError = staleError,
            capabilities = capabilities, selectedKeys = selectedKeys, busy = busy, job = job,
        )

    private fun setContent(
        state: GitChangesUiState,
        onEntryClick: (GitStatusEntry) -> Unit = {},
        onRetry: () -> Unit = {},
        onEntryLongClick: (GitStatusEntry) -> Unit = {},
        onStage: (List<GitStatusEntry>) -> Unit = {},
        onUnstage: (List<GitStatusEntry>) -> Unit = {},
        onSync: (() -> Unit)? = null,
        onCancelJob: () -> Unit = {},
        onDismissJob: () -> Unit = {},
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
                    onSync = onSync,
                    onCancelJob = onCancelJob,
                    onDismissJob = onDismissJob,
                )
            }
        }
    }

    private val syncCaps = GitCapabilities(
        stage = true, discard = true, commit = true, fetch = true, pull = true, push = true,
    )

    private fun job(
        status: String = GitJobResponse.STATUS_RUNNING,
        phase: String? = null,
        percent: Int? = null,
        line: String? = null,
        ok: Boolean? = null,
        errorCode: String? = null,
        message: String? = null,
    ) = GitJobResponse(
        job_id = "j1", session_id = "s1", op = GitJobResponse.OP_PUSH, status = status,
        phase = phase, percent = percent, line = line,
        ok = ok, error_code = errorCode, message = message,
    )

    @Test
    fun theUpstreamLine_isPlainText_untilTheAgentCanSync() {
        setContent(ready(capabilities = allCaps))

        composeRule.onNodeWithTag(GIT_CHANGES_UPSTREAM_TAG).assertIsDisplayed().assertHasNoClickAction()
        composeRule.onNodeWithTag(GIT_CHANGES_JOB_TAG).assertDoesNotExist()
    }

    @Test
    fun theUpstreamLine_opensTheSheet_onceTheAgentCanSync() {
        var opened = 0
        setContent(ready(capabilities = syncCaps), onSync = { opened += 1 })

        composeRule.onNodeWithTag(GIT_CHANGES_UPSTREAM_TAG).assertHasClickAction().performClick()

        assertEquals(1, opened)
    }

    @Test
    fun aRunningJob_bannersAboveTheList_withItsLineAndCancel() {
        var cancels = 0
        setContent(
            ready(
                capabilities = syncCaps,
                busy = true,
                job = GitJobBanner(
                    "j1",
                    job(phase = "Writing objects", percent = 45, line = "Writing objects:  45% (14/32)"),
                ),
            ),
            onSync = {},
            onCancelJob = { cancels += 1 },
        )

        composeRule.onNodeWithTag(GIT_CHANGES_JOB_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Pushing…").assertIsDisplayed()
        composeRule.onNodeWithText("Writing objects:  45% (14/32)").assertIsDisplayed()
        composeRule.onNodeWithTag(GIT_CHANGES_JOB_OPTIONS_TAG).assertDoesNotExist()
        composeRule.onNodeWithTag(GIT_CHANGES_JOB_DISMISS_TAG).assertDoesNotExist()

        composeRule.onNodeWithTag(GIT_CHANGES_JOB_CANCEL_TAG).performClick()

        assertEquals(1, cancels)
    }

    @Test
    fun aFailedJob_offersOptionsBackToTheSheet_andDismiss() {
        var opened = 0
        var dismissed = 0
        setContent(
            ready(
                capabilities = syncCaps,
                job = GitJobBanner(
                    "j1",
                    job(
                        status = GitJobResponse.STATUS_DONE, ok = false, errorCode = "rejected",
                        message = "! [rejected] main -> main (fetch first)",
                    ),
                ),
            ),
            onSync = { opened += 1 },
            onDismissJob = { dismissed += 1 },
        )

        composeRule.onNodeWithText("Push failed: The remote rejected the push.").assertIsDisplayed()
        composeRule.onNodeWithTag(GIT_CHANGES_JOB_CANCEL_TAG).assertDoesNotExist()

        composeRule.onNodeWithTag(GIT_CHANGES_JOB_OPTIONS_TAG).performClick()
        composeRule.onNodeWithTag(GIT_CHANGES_JOB_DISMISS_TAG).performClick()

        assertEquals(1, opened)
        assertEquals(1, dismissed)
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

/** The rebase banner above the list and a conflict row's Mark resolved. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class GitChangesScreenRebaseTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val conflict = GitStatusEntry(
        group = GitStatusEntry.GROUP_CONFLICTS, path = "src/login.py",
        code = "UU", index_status = "U", worktree_status = "U",
    )

    private val rebaseCaps = GitCapabilities(stage = true, discard = true, commit = true, rebase = true, resolve = true)
    private val resolverCaps = rebaseCaps.copy(conflicts = true)

    private fun ready(
        entries: List<GitStatusEntry> = listOf(conflict),
        state: String = GitRepoInfo.STATE_REBASING,
        capabilities: GitCapabilities = rebaseCaps,
        busy: Boolean = false,
        withDetails: Boolean = true,
        resolvedPaths: List<String> = emptyList(),
    ): GitChangesUiState.Ready {
        val status = GitStatusResponse(
            session_id = "s1",
            etag = "e1",
            repo = GitRepoInfo(
                toplevel = "/repo", git_dir = "/repo/.git", common_dir = "/repo/.git",
                head = GitHeadInfo(oid = "89abcdef", short_oid = "89abcde", detached = true),
                state = state,
                rebase = GitRebaseInfo(
                    branch = "topic",
                    onto_short_oid = "89abcde",
                    onto_ref = "main",
                    stopped_on = GitRebaseCommit("fedcba98", "fedcba9", "Fix the login redirect"),
                    step = 2,
                    total = 5,
                ).takeIf { state == GitRepoInfo.STATE_REBASING && withDetails },
            ),
            entries = entries,
            counts = GitStatusCounts(conflicts = entries.count { it.group == GitStatusEntry.GROUP_CONFLICTS }, total = entries.size),
            resolved_paths = resolvedPaths,
        )
        return GitChangesUiState.Ready(
            status, status.repo!!, sectionsFor(status), capabilities = capabilities, busy = busy,
        )
    }

    private fun setContent(
        state: GitChangesUiState,
        onResolve: (GitStatusEntry) -> Unit = {},
        onContinueRebase: () -> Unit = {},
        onRebaseStep: (RebasePromptStep) -> Unit = {},
        onRestore: (String) -> Unit = {},
    ) {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitChangesContent(
                    state = state,
                    onEntryClick = {},
                    onRetry = {},
                    onResolve = onResolve,
                    onContinueRebase = onContinueRebase,
                    onRebaseStep = onRebaseStep,
                    onRestore = onRestore,
                )
            }
        }
    }

    @Test
    fun aStoppedRebase_bannersWhereItStopped_holdsContinue_andOffersSkipAbortAndMarkResolved() {
        val steps = mutableListOf<RebasePromptStep>()
        val resolved = mutableListOf<GitStatusEntry>()
        setContent(ready(), onResolve = { resolved += it }, onRebaseStep = { steps += it })

        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Rebasing topic onto main").assertIsDisplayed()
        composeRule.onNodeWithText("Stopped at fedcba9 Fix the login redirect · 2/5").assertIsDisplayed()
        composeRule.onNodeWithText("1 conflict — fix the files, then Mark resolved.").assertIsDisplayed()
        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_CONTINUE_TAG).assertIsNotEnabled()

        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_SKIP_TAG).assertIsEnabled().performClick()
        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_ABORT_TAG).assertIsEnabled().performClick()
        composeRule.onNodeWithTag(gitChangesResolveTag(conflict)).assertIsEnabled().performClick()

        assertEquals(listOf(RebasePromptStep.Skip, RebasePromptStep.Abort), steps)
        assertEquals(listOf(conflict), resolved)
        composeRule.onNodeWithTag(gitChangesStageTag(conflict)).assertDoesNotExist()
    }

    @Test
    fun withNoConflictLeft_continueIsEnabled_andCallsBack() {
        var continued = 0
        setContent(ready(entries = emptyList()), onContinueRebase = { continued += 1 })

        composeRule.onNodeWithText("No conflicts left — Continue when ready.").assertIsDisplayed()
        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_CONTINUE_TAG).assertIsEnabled().performClick()

        assertEquals(1, continued)
    }

    @Test
    fun whileBusy_everyRebaseControlWaits() {
        setContent(ready(entries = emptyList(), busy = true))

        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_CONTINUE_TAG).assertIsNotEnabled()
        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_SKIP_TAG).assertIsNotEnabled()
        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_ABORT_TAG).assertIsNotEnabled()
    }

    @Test
    fun anAgentWithoutTheCapabilities_showsTheBannerAlone() {
        setContent(ready(capabilities = GitCapabilities(stage = true, discard = true, commit = true)))

        composeRule.onNodeWithText("Rebasing topic onto main").assertIsDisplayed()
        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_CONTINUE_TAG).assertDoesNotExist()
        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_SKIP_TAG).assertDoesNotExist()
        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_ABORT_TAG).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesResolveTag(conflict)).assertDoesNotExist()
    }

    @Test
    fun aRebaseWithNoDetails_pointsToTheTerminal_withoutContinueSkipOrAbort() {
        setContent(ready(entries = emptyList(), withDetails = false))

        composeRule.onNodeWithText("Rebase in progress").assertIsDisplayed()
        composeRule.onNodeWithText("Continue, skip or abort it in the terminal.").assertIsDisplayed()
        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_CONTINUE_TAG).assertDoesNotExist()
        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_SKIP_TAG).assertDoesNotExist()
        composeRule.onNodeWithTag(GIT_CHANGES_REBASE_ABORT_TAG).assertDoesNotExist()
    }

    @Test
    fun resolvedPaths_listUnderConflicts_eachWithItsRestore() {
        val restored = mutableListOf<String>()
        setContent(
            ready(capabilities = resolverCaps, resolvedPaths = listOf("docs/notes.md", "README.md")),
            onRestore = { restored += it },
        )

        composeRule.onNodeWithTag(GIT_CHANGES_RESOLVED_SECTION_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("notes.md").assertIsDisplayed()
        composeRule.onNodeWithText("docs").assertIsDisplayed()
        composeRule.onNodeWithTag(gitChangesRestoreTag("README.md")).assertIsEnabled()

        composeRule.onNodeWithTag(gitChangesRestoreTag("docs/notes.md")).assertIsEnabled().performClick()

        assertEquals(listOf("docs/notes.md"), restored)
    }

    @Test
    fun resolvedPaths_standAlone_whenEveryResolutionEqualsHead() {
        setContent(ready(entries = emptyList(), capabilities = resolverCaps, resolvedPaths = listOf("src/session.py")))

        composeRule.onNodeWithTag(GIT_CHANGES_RESOLVED_SECTION_TAG).assertIsDisplayed()
        composeRule.onNodeWithTag(gitChangesRestoreTag("src/session.py")).assertIsDisplayed()
        composeRule.onNodeWithTag(GIT_CHANGES_EMPTY_TAG).assertDoesNotExist()
    }

    @Test
    fun resolvedPaths_areHidden_onAnAgentWithoutTheResolver() {
        setContent(ready(resolvedPaths = listOf("src/session.py")))

        composeRule.onNodeWithTag(GIT_CHANGES_RESOLVED_SECTION_TAG).assertDoesNotExist()
        composeRule.onNodeWithTag(gitChangesRestoreTag("src/session.py")).assertDoesNotExist()
    }
}
