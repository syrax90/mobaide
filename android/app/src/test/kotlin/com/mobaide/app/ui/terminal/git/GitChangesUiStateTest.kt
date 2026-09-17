package com.mobaide.app.ui.terminal.git

import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.GitCommitAuthor
import com.mobaide.app.data.network.dto.GitCommitHead
import com.mobaide.app.data.network.dto.GitCommitResponse
import com.mobaide.app.data.network.dto.GitDiscardRequest
import com.mobaide.app.data.network.dto.GitHeadInfo
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.data.network.dto.GitJobResponse
import com.mobaide.app.data.network.dto.GitRebaseCommit
import com.mobaide.app.data.network.dto.GitRebaseInfo
import com.mobaide.app.data.network.dto.GitRebaseResponse
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusCounts
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.GitUpstreamInfo
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.data.repository.SessionGitException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * The pure half of the Changes screen: [truncatedNotice], the per-row and
 * per-section action rules, the selection subsets, the discard
 * prompt and the commit sheet's copy.
 */
class GitChangesUiStateTest {

    private fun untracked(path: String): GitStatusEntry = GitStatusEntry(
        group = GitStatusEntry.GROUP_UNTRACKED, path = path,
        code = "?", index_status = "?", worktree_status = "?",
    )

    private fun entry(group: String, path: String, code: String, origPath: String? = null) =
        GitStatusEntry(
            group = group, path = path, orig_path = origPath,
            code = code, index_status = code, worktree_status = ".",
        )

    private val conflict = entry(GitStatusEntry.GROUP_CONFLICTS, "c.py", "UU")
    private val rename = entry(GitStatusEntry.GROUP_STAGED, "src/new name.py", "R", "src/old.py")
    private val modified = entry(GitStatusEntry.GROUP_UNSTAGED, "m.py", "M")
    private val notes = untracked("notes.txt")

    private val allCaps = GitCapabilities(stage = true, discard = true, commit = true)

    private fun status(entries: List<GitStatusEntry>): GitStatusResponse =
        GitStatusResponse(session_id = "s1", etag = "e1", entries = entries)

    private fun gitError(code: String, message: String? = null) =
        SessionGitException.GitError(code, message, ApiException(409, ""))

    private val repoInfo = com.mobaide.app.data.network.dto.GitRepoInfo(
        toplevel = "/repo", git_dir = "/repo/.git", common_dir = "/repo/.git",
        head = com.mobaide.app.data.network.dto.GitHeadInfo(branch = "main"),
    )

    private fun ready(
        entries: List<GitStatusEntry>,
        capabilities: GitCapabilities = allCaps,
    ): GitChangesUiState.Ready {
        val status = status(entries)
        return GitChangesUiState.Ready(status, repoInfo, sectionsFor(status), capabilities = capabilities)
    }

    private fun truncated(
        entries: List<GitStatusEntry>,
        counts: GitStatusCounts?,
    ): GitStatusResponse = GitStatusResponse(
        session_id = "s1",
        etag = "e1",
        entries = entries,
        counts = counts,
        truncated = true,
    )

    @Test
    fun truncatedNotice_showsWhatArrived_againstALowerBoundTotal() {
        val status = truncated(
            entries = listOf(untracked("build/a.o"), untracked("build/b.o")),
            counts = GitStatusCounts(untracked = 6001, total = 6001),
        )

        assertEquals("Showing the first 2 of at least 6001 entries.", truncatedNotice(status))
    }

    @Test
    fun truncatedNotice_withoutAHigherTotal_onlySaysTheListWasCut() {
        // The byte cap hit before the entry cap: the count is a lower bound no
        // higher than the list itself, so no total is claimed.
        val byteCapped = truncated(
            entries = listOf(untracked("build/a.o"), untracked("build/b.o")),
            counts = GitStatusCounts(untracked = 2, total = 2),
        )

        assertEquals("List cut short after 2 entries.", truncatedNotice(byteCapped))
        assertEquals("List cut short after 0 entries.", truncatedNotice(truncated(emptyList(), counts = null)))
    }

    // MARK: - Row and section actions

    @Test
    fun rowActions_unstageAStagedRow_stageOrDiscardTheRest_andLeaveAConflictAlone() {
        assertEquals(RowActions(unstage = true), rowActions(rename, allCaps))
        assertEquals(RowActions(stage = true, discard = true), rowActions(modified, allCaps))
        assertEquals(RowActions(stage = true, discard = true), rowActions(notes, allCaps))
        assertEquals(RowActions.NONE, rowActions(conflict, allCaps))
        assertEquals(RowActions.NONE, rowActions(entry("ignored", "i.txt", "!"), allCaps))
    }

    @Test
    fun rowActions_markAConflictResolved_onlyOnAnAgentThatStagesAndGuardsTheMarkers() {
        val resolving = allCaps.copy(resolve = true)

        assertEquals(RowActions(resolve = true), rowActions(conflict, resolving))
        assertEquals(RowActions.NONE, rowActions(conflict, resolving.copy(stage = false)))
        assertEquals(RowActions.NONE, rowActions(conflict, allCaps))
        assertFalse(rowActions(modified, resolving).resolve)
        assertFalse(rowActions(rename, resolving).resolve)
    }

    @Test
    fun resolve_staysOutOfTheSelection_andOfTheSelectMode() {
        // Mark resolved is one file at a time: never a checkbox, never a toolbar action.
        val resolving = allCaps.copy(resolve = true)
        val onlyConflicts = ready(listOf(conflict), resolving)
        val everything = onlyConflicts.sections.flatMap { it.keys }.toSet()

        assertFalse(RowActions(resolve = true).any)
        assertFalse(onlyConflicts.hasActionableRows)
        assertEquals(
            SelectionSubsets(),
            selectionSubsets(onlyConflicts.sections, everything, resolving),
        )
    }

    @Test
    fun rowActions_gateEachActionOnItsOwnCapability() {
        val discardOnly = GitCapabilities(stage = false, discard = true, commit = true)
        val stageOnly = GitCapabilities(stage = true, discard = false, commit = false)

        assertEquals(RowActions(discard = true), rowActions(modified, discardOnly))
        assertEquals(RowActions.NONE, rowActions(rename, discardOnly))
        assertEquals(RowActions(stage = true), rowActions(modified, stageOnly))
        assertEquals(RowActions.NONE, rowActions(conflict, stageOnly))
        assertEquals(RowActions(unstage = true), rowActions(rename, stageOnly))
        assertEquals(RowActions.NONE, rowActions(modified, GitCapabilities.NONE))
        assertTrue(RowActions(discard = true).any)
        assertTrue(!RowActions.NONE.any)
    }

    @Test
    fun discardScopeFor_sendsUntrackedToClean_andTrackedToRestore() {
        assertEquals(GitDiscardRequest.SCOPE_UNTRACKED, discardScopeFor(notes))
        assertEquals(GitDiscardRequest.SCOPE_UNSTAGED, discardScopeFor(modified))
        assertEquals(GitDiscardRequest.SCOPE_UNSTAGED, discardScopeFor(rename))
    }

    @Test
    fun pathsFor_namesBothSidesOfARename_eachOnce() {
        // `git reset` on the new name alone would leave the deletion staged.
        val alsoOld = entry(GitStatusEntry.GROUP_UNSTAGED, "src/old.py", "M")

        assertEquals(
            listOf("src/new name.py", "src/old.py", "notes.txt"),
            pathsFor(listOf(rename, alsoOld, notes)),
        )
        assertEquals(emptyList<String>(), pathsFor(emptyList()))
    }

    @Test
    fun selectionSubsets_sortTheTickedRowsByTheActionEachCanTake() {
        val sections = sectionsFor(status(listOf(rename, modified, notes, conflict)))
        val everything = sections.flatMap { it.keys }.toSet()

        val subsets = selectionSubsets(sections, everything, allCaps)

        assertEquals(listOf(modified, notes), subsets.stage)
        assertEquals(listOf(rename), subsets.unstage)
        assertEquals(listOf(modified, notes), subsets.discard)
    }

    @Test
    fun selectionSubsets_ignoreUntickedRows_andActionsTheAgentLacks() {
        val sections = sectionsFor(status(listOf(rename, modified, notes)))

        val subsets = selectionSubsets(sections, setOf("unstaged:1:m.py"), allCaps.copy(discard = false))

        assertEquals(SelectionSubsets(stage = listOf(modified)), subsets)
    }

    @Test
    fun sectionAction_offersUnstageAllOnStaged_stageAllOnChangesAndUntracked_nothingElsewhere() {
        val sections = sectionsFor(
            status(listOf(conflict, rename, modified, notes, entry("ignored", "i.txt", "!"))),
        )

        val byGroup = sections.associate { it.group to sectionAction(it, allCaps) }

        assertEquals(
            mapOf(
                GitStatusEntry.GROUP_CONFLICTS to null,
                GitStatusEntry.GROUP_STAGED to SectionAction("Unstage all", stage = false),
                GitStatusEntry.GROUP_UNSTAGED to SectionAction("Stage all", stage = true),
                GitStatusEntry.GROUP_UNTRACKED to SectionAction("Stage all", stage = true),
                "ignored" to null,
            ),
            byGroup,
        )
        assertNull(sectionAction(sections[1], allCaps.copy(stage = false)))
    }

    @Test
    fun ready_isInSelectionMode_whileAnyRowIsTicked_andCountsTheStagedFiles() {
        val status = GitStatusResponse(
            session_id = "s1", etag = "e1", entries = listOf(rename),
            counts = GitStatusCounts(staged = 3, total = 3),
        )
        val repo = com.mobaide.app.data.network.dto.GitRepoInfo(
            toplevel = "/repo", git_dir = "/repo/.git", common_dir = "/repo/.git",
            head = com.mobaide.app.data.network.dto.GitHeadInfo(branch = "main"),
        )
        val ready = GitChangesUiState.Ready(status, repo, sectionsFor(status))

        assertTrue(!ready.inSelectionMode)
        assertEquals(3, ready.stagedCount)
        assertTrue(ready.copy(selectedKeys = setOf("staged:1:src/new name.py")).inSelectionMode)
        assertEquals(0, ready.copy(status = status.copy(counts = null)).stagedCount)
    }

    @Test
    fun ready_isInSelectionMode_fromTheTopBarSelect_beforeAnythingIsTicked() {
        val entering = ready(listOf(modified)).copy(selecting = true)

        assertTrue(entering.inSelectionMode)
        assertEquals(emptySet<String>(), entering.selectedKeys)
        assertTrue(!entering.copy(selecting = false).inSelectionMode)
    }

    @Test
    fun ready_hasActionableRows_onlyWhenSomeShownRowHasAControl() {
        val discardOnly = GitCapabilities(stage = false, discard = true, commit = false)

        assertTrue(ready(listOf(conflict, rename)).hasActionableRows)
        assertTrue(ready(listOf(modified), discardOnly).hasActionableRows)
        assertTrue(!ready(listOf(conflict)).hasActionableRows)
        assertTrue(!ready(listOf(rename), discardOnly).hasActionableRows)
        assertTrue(!ready(listOf(rename, modified, notes), GitCapabilities.NONE).hasActionableRows)
        assertTrue(!ready(emptyList()).hasActionableRows)
    }

    // MARK: - Discard prompt

    @Test
    fun discardPrompt_namesTheOneFile_andSaysWhetherItIsDeletedOrReverted() {
        val untrackedPrompt = DiscardPrompt(listOf(notes))
        assertEquals("Delete untracked file?", untrackedPrompt.title)
        assertEquals(
            "notes.txt is not tracked by git and will be deleted. This cannot be undone.",
            untrackedPrompt.body,
        )

        val trackedPrompt = DiscardPrompt(listOf(modified))
        assertEquals("Discard changes?", trackedPrompt.title)
        assertEquals("Changes to m.py will be thrown away. This cannot be undone.", trackedPrompt.body)
    }

    @Test
    fun discardPrompt_countsAMultiFileDiscard() {
        val prompt = DiscardPrompt(listOf(modified, notes))

        assertEquals("Discard 2 files?", prompt.title)
        assertEquals(
            "Their working-tree changes will be thrown away and untracked ones deleted. " +
                "This cannot be undone.",
            prompt.body,
        )
    }

    // MARK: - Commit copy

    @Test
    fun composeCommitMessage_joinsSubjectAndBody_theWayGitCommitReadsThem() {
        assertEquals("Fix it", composeCommitMessage("  Fix it  ", ""))
        assertEquals("Fix it", composeCommitMessage("Fix it", "  \n"))
        assertEquals("Fix it\n\nWhy.", composeCommitMessage("Fix it", "  Why.\n"))
    }

    @Test
    fun commitButtonLabel_countsTheStagedFiles_orReadsAsAnAmend() {
        assertEquals("Commit 0 files", commitButtonLabel(0, amend = false))
        assertEquals("Commit 1 file", commitButtonLabel(1, amend = false))
        assertEquals("Commit 3 files", commitButtonLabel(3, amend = false))
        assertEquals("Amend commit", commitButtonLabel(3, amend = true))
    }

    @Test
    fun commitNotice_andAuthorLine_matchTheOtherClients() {
        assertEquals(
            "Committed abc1234 · Fix it",
            commitNotice(GitCommitResponse("abc1234def", "abc1234", "Fix it")),
        )
        assertEquals(
            "Committing as Ada Lovelace <ada@example.com>",
            authorLine(GitCommitAuthor("Ada Lovelace", "ada@example.com")),
        )
        assertEquals(
            listOf(
                "git config --global user.name \"Your Name\"",
                "git config --global user.email \"you@example.com\"",
            ),
            IDENTITY_FIX_COMMANDS,
        )
        assertEquals("", GitCommitHead("abc", "abc", "subject").body)
    }

    // MARK: - Refusal copy

    @Test
    fun refusalCopy_phrasesEach409Code() {
        assertEquals(
            "Git doesn't know who you are on the agent machine.",
            refusalCopy(SessionGitException.CODE_IDENTITY_REQUIRED, null),
        )
        assertEquals("Resolve the conflicts first.", refusalCopy(SessionGitException.CODE_UNMERGED, null))
        assertEquals("Nothing is staged.", refusalCopy(SessionGitException.CODE_NOTHING_TO_COMMIT, null))
        assertEquals(
            "There is no commit to amend yet.",
            refusalCopy(SessionGitException.CODE_NO_HEAD, null),
        )
        assertEquals(
            "A commit hook rejected the commit.",
            refusalCopy(SessionGitException.CODE_HOOK_FAILED, "lint: nope"),
        )
        assertEquals(
            "Another git process is running — try again.",
            refusalCopy(SessionGitException.CODE_INDEX_LOCKED, null),
        )
        assertEquals(
            "That file changed under you — refreshed.",
            refusalCopy(SessionGitException.CODE_PATHSPEC_MISMATCH, null),
        )
        assertEquals("Enter a commit summary.", refusalCopy(SessionGitException.CODE_EMPTY_MESSAGE, null))
    }

    @Test
    fun refusalCopy_namesTheFileThatStillHoldsConflictMarkers() {
        assertEquals(
            "src/login.py still has conflict markers — finish resolving it first.",
            refusalCopy(SessionGitException.CODE_CONFLICT_MARKERS, "src/login.py"),
        )
        assertEquals(
            "The file still has conflict markers — finish resolving it first.",
            refusalCopy(SessionGitException.CODE_CONFLICT_MARKERS, null),
        )
    }

    @Test
    fun refusalCopy_namesAnUnknownCode_withTheGitOutputWhenThereIsAny() {
        assertEquals("git refused the request (rate_limited).", refusalCopy("rate_limited", null))
        assertEquals(
            "git refused the request (rate_limited): slow down",
            refusalCopy("rate_limited", "slow down"),
        )
    }

    @Test
    fun sessionGitErrorCopy_reachesTheRefusalCopy_forA409() {
        assertEquals(
            "Another git process is running — try again.",
            sessionGitErrorCopy(gitError(SessionGitException.CODE_INDEX_LOCKED, "index.lock exists")),
        )
    }

    @Test
    fun mutationErrorCopy_readsGitTooOld_asTheMutationFloor_notThePollFloor() {
        assertEquals(
            "Staging needs git 2.26 or newer on the agent machine.",
            mutationErrorCopy(gitError(SessionGitException.CODE_GIT_TOO_OLD)),
        )
        assertEquals(
            "The agent host needs git 2.15 or newer.",
            sessionGitErrorCopy(gitError(SessionGitException.CODE_GIT_TOO_OLD)),
        )
    }

    @Test
    fun mutationErrorCopy_defersToTheViewerCopy_forEverythingElse() {
        assertEquals(
            "Another git process is running — try again.",
            mutationErrorCopy(gitError(SessionGitException.CODE_INDEX_LOCKED)),
        )
        assertEquals(
            "Could not reach the agent. Check the connection and try again.",
            mutationErrorCopy(SessionGitException.Network(IOException("down"))),
        )
    }
}

/**
 * The pure half of the Sync sheet and the job banner: the three rows and
 * their reasons, the job copy shared byte-for-byte with iOS and the web, the
 * follow-ups a failure allows, and what [GitJobBanner] derives.
 */
class GitChangesUiStateSyncTest {

    private val syncCaps = GitCapabilities(
        stage = true, discard = true, commit = true, fetch = true, pull = true, push = true,
    )
    private val noSync = GitCapabilities(stage = true, discard = true, commit = true)

    private fun repo(
        upstream: GitUpstreamInfo? = null,
        head: GitHeadInfo = GitHeadInfo(oid = "abc1234def", short_oid = "abc1234", branch = "main"),
    ) = GitRepoInfo(
        toplevel = "/repo", git_dir = "/repo/.git", common_dir = "/repo/.git",
        head = head, upstream = upstream,
    )

    private fun tracking(ahead: Int?, behind: Int?) = repo(GitUpstreamInfo("origin/main", ahead, behind))

    private fun job(
        op: String = GitJobResponse.OP_PUSH,
        status: String = GitJobResponse.STATUS_RUNNING,
        ok: Boolean? = null,
        errorCode: String? = null,
        message: String? = null,
    ) = GitJobResponse(
        job_id = "j1", session_id = "s1", op = op, status = status,
        ok = ok, error_code = errorCode, message = message,
    )

    private fun done(op: String = GitJobResponse.OP_PUSH, message: String? = "main -> main") =
        job(op = op, status = GitJobResponse.STATUS_DONE, ok = true, message = message)

    private fun failed(code: String, op: String = GitJobResponse.OP_PUSH, message: String? = null) =
        job(op = op, status = GitJobResponse.STATUS_DONE, ok = false, errorCode = code, message = message)

    private fun rowsOf(repo: GitRepoInfo?, caps: GitCapabilities = syncCaps) = syncRows(repo, caps)
    private fun pull(repo: GitRepoInfo?) = rowsOf(repo).first { it.op == GitJobResponse.OP_PULL }
    private fun push(repo: GitRepoInfo?) = rowsOf(repo).first { it.op == GitJobResponse.OP_PUSH }

    @Test
    fun syncRows_existOnlyForTheCapabilitiesTheAgentAdvertises() {
        assertEquals(emptyList<SyncRow>(), rowsOf(tracking(1, 1), noSync))
        assertEquals(listOf("fetch"), rowsOf(tracking(1, 1), GitCapabilities(fetch = true)).map { it.op })
        assertEquals(listOf("fetch", "pull", "push"), rowsOf(tracking(1, 1)).map { it.op })
    }

    @Test
    fun syncRows_alwaysLetAFetchThrough() {
        assertEquals(
            SyncRow("fetch", "Fetch", "Update remote-tracking branches", enabled = true),
            rowsOf(repo())[0],
        )
    }

    @Test
    fun syncRows_enablePullOnlyWithCommitsBehind_andSayWhyNotOtherwise() {
        assertEquals(
            SyncRow("pull", "Pull", "Fast-forward 2 commits from origin/main", enabled = true),
            pull(tracking(0, 2)),
        )
        assertEquals("Fast-forward 1 commit from origin/main", pull(tracking(0, 1)).detail)
        assertEquals(SyncRow("pull", "Pull", "Nothing to pull", enabled = false), pull(tracking(3, 0)))
        assertEquals(SyncRow("pull", "Pull", "No upstream", enabled = false), pull(repo()))
        assertEquals(SyncRow("pull", "Pull", "Upstream gone", enabled = false), pull(tracking(null, null)))
    }

    @Test
    fun syncRows_offerPublishBranchWithoutAnUpstream_andPushOnlyWithCommitsAhead() {
        assertEquals(
            SyncRow("push", "Publish branch", "Push and set the upstream", enabled = true),
            push(repo()),
        )
        assertEquals(SyncRow("push", "Push", "3 commits to origin/main", enabled = true), push(tracking(3, 0)))
        assertEquals("1 commit to origin/main", push(tracking(1, 2)).detail)
        assertEquals(SyncRow("push", "Push", "Nothing to push", enabled = false), push(tracking(0, 2)))
        assertEquals(SyncRow("push", "Push", "Recreate origin/main", enabled = true), push(tracking(null, null)))
    }

    @Test
    fun syncRows_blockPullAndPush_neverFetch_onADetachedOrUnbornHead_andWithoutARepo() {
        val detached = repo(head = GitHeadInfo(short_oid = "abc1234", detached = true))
        val initial = repo(head = GitHeadInfo(branch = "main", initial = true))

        assertEquals(
            listOf(
                Triple("fetch", true, "Update remote-tracking branches"),
                Triple("pull", false, "Detached HEAD"),
                Triple("push", false, "Detached HEAD"),
            ),
            rowsOf(detached).map { Triple(it.op, it.enabled, it.detail) },
        )
        assertEquals(listOf("No commits yet", "No commits yet"), rowsOf(initial).drop(1).map { it.detail })
        assertEquals(
            listOf(true to "Update remote-tracking branches", false to "Repository unavailable", false to "Repository unavailable"),
            rowsOf(null).map { it.enabled to it.detail },
        )
    }

    @Test
    fun jobTitles_perOp_runningAndFinished() {
        assertEquals(listOf("Fetching…", "Pulling…", "Pushing…"), listOf("fetch", "pull", "push").map(::jobTitle))
        assertEquals("Fetched", jobDoneTitle("fetch", ok = true))
        assertEquals("Pulled", jobDoneTitle("pull", ok = true))
        assertEquals("Pushed", jobDoneTitle("push", ok = true))
        assertEquals("Fetch failed", jobDoneTitle("fetch", ok = false))
        assertEquals("Pull failed", jobDoneTitle("pull", ok = false))
        assertEquals("Push failed", jobDoneTitle("push", ok = false))
    }

    @Test
    fun jobErrorCopy_phrasesEveryCode_identicallyToTheOtherClients() {
        assertEquals("Cancelled.", jobErrorCopy("cancelled", null))
        assertEquals("git took too long to answer. Try again.", jobErrorCopy("git_timeout", null))
        assertEquals(
            "No credentials for this remote. Configure a credential helper or SSH agent on the agent host.",
            jobErrorCopy("auth_required", null),
        )
        assertEquals("Couldn't reach the remote.", jobErrorCopy("network", null))
        assertEquals("The remote doesn't exist.", jobErrorCopy("remote_not_found", null))
        assertEquals("The remote rejected the push.", jobErrorCopy("rejected", null))
        assertEquals("Your branch is behind the remote.", jobErrorCopy("not_fast_forward", null))
        assertEquals("Stopped on conflicts — resolve them in the terminal.", jobErrorCopy("conflicts", null))
        assertEquals("Commit or stash your changes first.", jobErrorCopy("dirty_worktree", null))
        assertEquals("Resolve the conflicts first.", jobErrorCopy("unmerged", null))
        assertEquals("A rebase or merge is already in progress.", jobErrorCopy("operation_in_progress", null))
        assertEquals("This branch has no upstream.", jobErrorCopy("no_upstream", null))
        assertEquals("This repository has no remote.", jobErrorCopy("no_remote", null))
        assertEquals("Detached HEAD — check out a branch first.", jobErrorCopy("detached_head", null))
        assertEquals("No commits yet.", jobErrorCopy("no_head", null))
        assertEquals("A pre-push hook rejected the push.", jobErrorCopy("hook_failed", null))
        assertEquals("Another git process is running — try again.", jobErrorCopy("index_locked", null))
    }

    @Test
    fun jobErrorCopy_fallsBackToTheMessage_forGitErrorAndUnknownCodes() {
        assertEquals("fatal: boom", jobErrorCopy("git_error", "fatal: boom"))
        assertEquals("git failed.", jobErrorCopy("git_error", null))
        assertEquals("details", jobErrorCopy("something_new", "details"))
        assertEquals("git failed.", jobErrorCopy("something_new", ""))
        assertEquals("git failed.", jobErrorCopy(null, null))
    }

    @Test
    fun jobNotice_isTheTitle_theSummary_orTheFailureCopy() {
        assertEquals("Pushing…", jobNotice(job()))
        assertEquals("Fetching…", jobNotice(job(op = "fetch")))
        assertEquals("Pushed · main -> main", jobNotice(done()))
        assertEquals("Pushed", jobNotice(done(message = null)))
        assertEquals("Pulled", jobNotice(done(op = "pull", message = "")))
        assertEquals(
            "Push failed: The remote rejected the push.",
            jobNotice(failed("rejected", message = "! [rejected] main -> main (fetch first)")),
        )
        assertEquals("Fetch failed: fatal: boom", jobNotice(failed("git_error", op = "fetch", message = "fatal: boom")))
    }

    @Test
    fun showsJobOutput_onlyWhenTheCopyDidNotAlreadySayIt() {
        assertTrue(showsJobOutput(failed("rejected", message = "! [rejected] main -> main")))
        assertFalse(showsJobOutput(failed("git_error", message = "fatal: boom")))
        assertTrue(showsJobOutput(failed("cancelled", message = "cancelled")))
        assertFalse(showsJobOutput(failed("no_remote", message = null)))
        assertFalse(showsJobOutput(done()))
        assertFalse(showsJobOutput(job(message = "x")))
    }

    @Test
    fun jobFollowUps_rebaseOrMergeBehindTheRemote_leaseTooAfterAPush_leaseAloneAfterARejection() {
        assertEquals(
            listOf(SyncFollowUp.PullRebase, SyncFollowUp.PullMerge),
            jobFollowUps(failed("not_fast_forward", op = "pull")),
        )
        assertEquals(
            listOf(SyncFollowUp.PullRebase, SyncFollowUp.PullMerge, SyncFollowUp.ForcePush),
            jobFollowUps(failed("not_fast_forward", op = "push")),
        )
        assertEquals(listOf(SyncFollowUp.ForcePush), jobFollowUps(failed("rejected", op = "push")))
        assertEquals(emptyList<SyncFollowUp>(), jobFollowUps(failed("rejected", op = "pull")))
        assertEquals(emptyList<SyncFollowUp>(), jobFollowUps(failed("conflicts", op = "pull")))
        assertEquals(emptyList<SyncFollowUp>(), jobFollowUps(done()))
        assertEquals(emptyList<SyncFollowUp>(), jobFollowUps(job()))
    }

    @Test
    fun jobFollowUps_dropThePulls_whenTheStatusSaysNoUpstream_neverWhenItIsUnavailable() {
        assertEquals(
            listOf(SyncFollowUp.ForcePush),
            jobFollowUps(failed("not_fast_forward", op = "push"), repo()),
        )
        assertEquals(emptyList<SyncFollowUp>(), jobFollowUps(failed("not_fast_forward", op = "pull"), repo()))
        assertEquals(listOf(SyncFollowUp.ForcePush), jobFollowUps(failed("rejected", op = "push"), repo()))
        assertEquals(
            listOf(SyncFollowUp.ForcePush),
            jobFollowUps(failed("not_fast_forward", op = "push"), tracking(null, null)),
        )
        assertEquals(
            emptyList<SyncFollowUp>(),
            jobFollowUps(failed("not_fast_forward", op = "pull"), tracking(null, null)),
        )
        assertEquals(
            listOf(SyncFollowUp.PullRebase, SyncFollowUp.PullMerge, SyncFollowUp.ForcePush),
            jobFollowUps(failed("not_fast_forward", op = "push"), tracking(1, 1)),
        )
        assertEquals(
            listOf(SyncFollowUp.PullRebase, SyncFollowUp.PullMerge),
            jobFollowUps(failed("not_fast_forward", op = "pull"), tracking(0, 1)),
        )
        assertEquals(
            listOf(SyncFollowUp.PullRebase, SyncFollowUp.PullMerge, SyncFollowUp.ForcePush),
            jobFollowUps(failed("not_fast_forward", op = "push"), null),
        )
    }

    @Test
    fun followUps_areLabelledAndConfirmed_namingTheUpstreamAndBranchWhenKnown() {
        assertEquals("Pull with rebase…", followUpLabel(SyncFollowUp.PullRebase))
        assertEquals("Pull with merge…", followUpLabel(SyncFollowUp.PullMerge))
        assertEquals("Force push (with lease)…", followUpLabel(SyncFollowUp.ForcePush))
        assertEquals(
            SyncPrompt("Pull with rebase?", "Replays your commits on top of origin/main.", "Rebase"),
            followUpPrompt(SyncFollowUp.PullRebase, tracking(1, 1)),
        )
        assertEquals(
            SyncPrompt("Pull with merge?", "Merges origin/main into main.", "Merge"),
            followUpPrompt(SyncFollowUp.PullMerge, tracking(1, 1)),
        )
        assertEquals(
            SyncPrompt(
                "Force push?",
                "Overwrites origin/main with your branch. Proceeds only if the remote hasn't moved since your last fetch.",
                "Force push",
            ),
            followUpPrompt(SyncFollowUp.ForcePush, tracking(1, 1)),
        )
        assertEquals("Merges the upstream into your branch.", followUpPrompt(SyncFollowUp.PullMerge, null).body)
    }

    @Test
    fun gitJobBanner_derivesRunningFailedAndText_andALostJobHoldsNothing() {
        val running = GitJobBanner("j1", job())
        assertTrue(running.running)
        assertFalse(running.failed)
        assertEquals("Pushing…", running.text)

        val waiting = GitJobBanner("j1", null)
        assertTrue(waiting.running)
        assertEquals("Syncing…", waiting.text)

        val ok = GitJobBanner("j1", done())
        assertFalse(ok.running)
        assertFalse(ok.failed)
        assertEquals("Pushed · main -> main", ok.text)

        val rejected = GitJobBanner("j1", failed("rejected"))
        assertFalse(rejected.running)
        assertTrue(rejected.failed)

        val lost = GitJobBanner("j1", job(), lost = true)
        assertFalse(lost.running)
        assertTrue(lost.failed)
        assertEquals("Lost track of the job — refresh to see the result.", lost.text)
    }

    @Test
    fun refusalCopy_phrasesTheTwoJobRefusals_andTheResultStaysSixSeconds() {
        assertEquals("Another fetch, pull or push is still running.", refusalCopy("job_running", "ab12cd34"))
        assertEquals("Lost track of the job — refresh to see the result.", refusalCopy("job_not_found", null))
        assertEquals(6_000L, JOB_RESULT_DISMISS_MS)
    }
}

/**
 * The pure half of the rebase flow: the sheet's picker and blocker, the
 * banner, the Skip / Abort confirmations and the outcome and error copy —
 * the same strings as iOS and the web.
 */
class GitChangesUiStateRebaseTest {

    private fun item(kind: String, branch: String?, isCurrent: Boolean = false) =
        GitItem(kind = kind, branch = branch, is_current = isCurrent)

    private val stopped = GitRebaseCommit(oid = "fedcba98", short_oid = "fedcba9", subject = "Fix the login redirect")

    private fun info(
        branch: String? = "topic",
        ontoRef: String? = "main",
        ontoShortOid: String? = "89abcde",
        stoppedOn: GitRebaseCommit? = stopped,
        step: Int? = 2,
        total: Int? = 5,
    ) = GitRebaseInfo(
        branch = branch, onto_oid = "89abcdef0123", onto_short_oid = ontoShortOid,
        onto_ref = ontoRef, stopped_on = stoppedOn, step = step, total = total,
    )

    private fun repo(
        state: String = GitRepoInfo.STATE_CLEAN,
        head: GitHeadInfo = GitHeadInfo(oid = "abc1234def", short_oid = "abc1234", branch = "topic"),
        rebase: GitRebaseInfo? = null,
    ) = GitRepoInfo(
        toplevel = "/repo", git_dir = "/repo/.git", common_dir = "/repo/.git",
        head = head, state = state, rebase = rebase,
    )

    private fun rebasing(rebase: GitRebaseInfo? = info()) = repo(
        state = GitRepoInfo.STATE_REBASING,
        head = GitHeadInfo(oid = "89abcdef", short_oid = "89abcde", detached = true),
        rebase = rebase,
    )

    private fun status(repo: GitRepoInfo? = repo(), counts: GitStatusCounts? = GitStatusCounts()) =
        GitStatusResponse(session_id = "s1", etag = "e1", repo = repo, counts = counts)

    private fun gitError(code: String, message: String? = null) =
        SessionGitException.GitError(code, message, ApiException(409, ""))

    // MARK: - Sheet

    @Test
    fun rebaseTargets_listLocalBranchesFirst_thenRemoteOnesLabelledAsSuch_eachSentAsAFullRef() {
        val items = listOf(
            item(GitItem.KIND_REMOTE_BRANCH, "origin/main"),
            item(GitItem.KIND_BRANCH_WITH_WORKTREE, "topic-b"),
            item(GitItem.KIND_BRANCH_ONLY, "main"),
            item(GitItem.KIND_REMOTE_BRANCH, "origin/topic"),
        )

        assertEquals(
            listOf(
                RebaseTarget("refs/heads/topic-b", "topic-b"),
                RebaseTarget("refs/heads/main", "main"),
                RebaseTarget("refs/remotes/origin/main", "origin/main (remote)"),
                RebaseTarget("refs/remotes/origin/topic", "origin/topic (remote)"),
            ),
            rebaseTargets(items, currentBranch = "topic"),
        )
    }

    @Test
    fun rebaseTargets_leaveOutTheSessionsOwnBranchByName_notTheRootsCurrentRow() {
        // A linked-worktree session on `topic`: the project root has `main` checked out.
        val items = listOf(
            item(GitItem.KIND_BRANCH_ONLY, "main", isCurrent = true),
            item(GitItem.KIND_BRANCH_WITH_WORKTREE, "topic"),
        )

        assertEquals(
            listOf(RebaseTarget("refs/heads/main", "main")),
            rebaseTargets(items, currentBranch = "topic"),
        )
    }

    @Test
    fun rebaseTargets_skipOrphanWorktreesAndBranchlessRows_andKeepEverythingOnADetachedHead() {
        val items = listOf(
            item(GitItem.KIND_ORPHAN_WORKTREE, null),
            item(GitItem.KIND_BRANCH_ONLY, null),
            item(GitItem.KIND_BRANCH_ONLY, "main"),
        )

        assertEquals(
            listOf(RebaseTarget("refs/heads/main", "main")),
            rebaseTargets(items, currentBranch = null),
        )
        assertEquals(emptyList<RebaseTarget>(), rebaseTargets(emptyList(), currentBranch = "main"))
    }

    @Test
    fun shortRefName_dropsALeadingHeadsOrRemotesPrefix_once() {
        assertEquals("main", shortRefName("refs/heads/main"))
        assertEquals("feature/login", shortRefName("refs/heads/feature/login"))
        assertEquals("origin/main", shortRefName("refs/remotes/origin/main"))
        assertEquals("refs/remotes/x", shortRefName("refs/heads/refs/remotes/x"))
    }

    @Test
    fun shortRefName_leavesATypedRefATagOrANonLeadingPrefixAsItIs() {
        assertEquals("main", shortRefName("main"))
        assertEquals("origin/main", shortRefName("origin/main"))
        assertEquals("refs/tags/v1.2.0", shortRefName("refs/tags/v1.2.0"))
        assertEquals("topic/refs/heads/main", shortRefName("topic/refs/heads/main"))
    }

    @Test
    fun rebaseBlocker_letsACleanBranchWithCommitsRebase_untrackedFilesAndAll() {
        assertNull(rebaseBlocker(status()))
        assertNull(rebaseBlocker(status(counts = GitStatusCounts(untracked = 3, total = 3))))
        assertNull(rebaseBlocker(status(counts = null)))
    }

    @Test
    fun rebaseBlocker_namesWhatIsInTheWay_theRepositoryFirst() {
        assertEquals("Repository unavailable.", rebaseBlocker(null))
        assertEquals("Repository unavailable.", rebaseBlocker(status(repo = null)))
        assertEquals(
            "A rebase or merge is already in progress.",
            rebaseBlocker(status(repo(state = GitRepoInfo.STATE_MERGING))),
        )
        assertEquals("A rebase or merge is already in progress.", rebaseBlocker(status(rebasing())))
        assertEquals(
            "This branch has no commits yet.",
            rebaseBlocker(status(repo(head = GitHeadInfo(branch = "main", initial = true)))),
        )
        for (counts in listOf(
            GitStatusCounts(staged = 1, total = 1),
            GitStatusCounts(unstaged = 2, total = 2),
            GitStatusCounts(conflicts = 1, total = 1),
        )) {
            assertEquals("Commit or discard your changes first.", rebaseBlocker(status(counts = counts)))
        }
    }

    // MARK: - Banner

    @Test
    fun rebaseBanner_isAbsentUnlessTheRepositoryIsRebasing() {
        assertNull(rebaseBanner(repo(), 0))
        assertNull(rebaseBanner(repo(state = GitRepoInfo.STATE_MERGING), 1))
        assertNull(rebaseBanner(null, 0))
    }

    @Test
    fun rebaseBanner_namesTheBranchBaseStopAndStep_withAHintForTheConflictsLeft() {
        assertEquals(
            RebaseBanner(
                title = "Rebasing topic onto main",
                progress = "Stopped at fedcba9 Fix the login redirect · 2/5",
                hint = "1 conflict — fix the files, then Mark resolved.",
                actionable = true,
            ),
            rebaseBanner(rebasing(), 1),
        )
        assertEquals("3 conflicts — fix the files, then Mark resolved.", rebaseBanner(rebasing(), 3)?.hint)
        assertEquals("No conflicts left — Continue when ready.", rebaseBanner(rebasing(), 0)?.hint)
    }

    @Test
    fun rebaseBanner_fallsBackToTheShortOid_thenDropsWhatTheAgentCouldNotRead() {
        assertEquals("Rebasing topic onto 89abcde", rebaseBanner(rebasing(info(ontoRef = null)), 0)?.title)
        assertEquals(
            "Rebasing topic",
            rebaseBanner(rebasing(info(ontoRef = null, ontoShortOid = null)), 0)?.title,
        )
        assertEquals("Rebasing onto main", rebaseBanner(rebasing(info(branch = null)), 0)?.title)
        assertEquals("2/5", rebaseBanner(rebasing(info(stoppedOn = null)), 0)?.progress)
        assertEquals(
            "Stopped at fedcba9 Fix the login redirect",
            rebaseBanner(rebasing(info(step = null)), 0)?.progress,
        )
    }

    @Test
    fun rebaseBanner_withNoDetails_asAGitAmSessionReportsIt_pointsToTheTerminalWhateverTheConflicts() {
        val toTerminal = RebaseBanner(
            "Rebase in progress", null, "Continue, skip or abort it in the terminal.", actionable = false,
        )

        assertEquals(toTerminal, rebaseBanner(rebasing(null), 0))
        assertEquals(toTerminal, rebaseBanner(rebasing(null), 2))
    }

    @Test
    fun rebaseBanner_keepsTheControls_forARebaseWhoseDetailsTheAgentCouldNotRead() {
        assertEquals(
            RebaseBanner(
                "Rebase in progress", null, "2 conflicts — fix the files, then Mark resolved.", actionable = true,
            ),
            rebaseBanner(rebasing(GitRebaseInfo()), 2),
        )
    }

    // MARK: - Confirmations

    @Test
    fun rebaseStepPrompt_skipNamesTheCommitItLeavesOut_abortWarnsTheResolutionsGo() {
        assertEquals(
            SyncPrompt(
                "Skip this commit?",
                "fedcba9 “Fix the login redirect” will be left out of topic.",
                "Skip commit",
            ),
            rebaseStepPrompt(RebasePromptStep.Skip, rebasing()),
        )
        assertEquals(
            "The commit the rebase stopped at will be left out of topic.",
            rebaseStepPrompt(RebasePromptStep.Skip, rebasing(info(stoppedOn = null))).body,
        )
        assertEquals(
            SyncPrompt(
                "Abort the rebase?",
                "topic goes back to where it was before the rebase. Conflict resolutions made so far are discarded.",
                "Abort rebase",
            ),
            rebaseStepPrompt(RebasePromptStep.Abort, rebasing()),
        )
    }

    @Test
    fun rebasedBranch_isTheRebasedOne_thenHeads_thenAGenericName() {
        assertEquals("topic", rebasedBranch(rebasing()))
        assertEquals("topic", rebasedBranch(repo()))
        assertEquals("the branch", rebasedBranch(rebasing(null)))
        assertEquals("the branch", rebasedBranch(null))
        assertEquals(
            "the branch goes back to where it was before the rebase. Conflict resolutions made so far are discarded.",
            rebaseStepPrompt(RebasePromptStep.Abort, null).body,
        )
    }

    // MARK: - Outcome and error copy

    @Test
    fun rebaseOutcomeNotice_reportsAFinishedStartWithTheBase_aFinishedStepWithoutIt() {
        val rebased = GitRebaseResponse(GitRebaseResponse.OUTCOME_REBASED)

        assertEquals("Rebased topic onto main.", rebaseOutcomeNotice(rebased, "topic", "main"))
        assertEquals("Rebase finished.", rebaseOutcomeNotice(rebased, "topic", null))
        assertEquals(
            "topic is already up to date with main.",
            rebaseOutcomeNotice(GitRebaseResponse(GitRebaseResponse.OUTCOME_UP_TO_DATE), "topic", "main"),
        )
        assertEquals(
            "Rebase aborted — topic is back where it started.",
            rebaseOutcomeNotice(GitRebaseResponse(GitRebaseResponse.OUTCOME_ABORTED), "topic", null),
        )
        assertEquals(
            "Rebase: future_outcome",
            rebaseOutcomeNotice(GitRebaseResponse("future_outcome"), "topic", null),
        )
    }

    @Test
    fun rebaseOutcomeNotice_namesABasePickedAsAFullRefByItsShortName() {
        assertEquals(
            "Rebased topic onto main.",
            rebaseOutcomeNotice(GitRebaseResponse(GitRebaseResponse.OUTCOME_REBASED), "topic", "refs/heads/main"),
        )
        assertEquals(
            "topic is already up to date with origin/main.",
            rebaseOutcomeNotice(
                GitRebaseResponse(GitRebaseResponse.OUTCOME_UP_TO_DATE),
                "topic",
                "refs/remotes/origin/main",
            ),
        )
    }

    @Test
    fun rebaseOutcomeNotice_reportsAStopOnItsSubject_orGitsExplanationWhenItWasNotAConflict() {
        assertEquals(
            "Rebase stopped at “Fix the login redirect” — resolve the conflicts, then Continue.",
            rebaseOutcomeNotice(GitRebaseResponse(GitRebaseResponse.OUTCOME_STOPPED, info()), "topic", "main"),
        )
        assertEquals(
            "Rebase stopped: error: The following untracked working tree files would be overwritten by merge:",
            rebaseOutcomeNotice(
                GitRebaseResponse(
                    GitRebaseResponse.OUTCOME_STOPPED,
                    info(),
                    "error: The following untracked working tree files would be overwritten by merge:",
                ),
                "topic",
                "main",
            ),
        )
        assertEquals(
            "Rebase stopped — resolve the conflicts, then Continue.",
            rebaseOutcomeNotice(GitRebaseResponse(GitRebaseResponse.OUTCOME_STOPPED, null, ""), "topic", null),
        )
    }

    @Test
    fun rebaseErrorCopy_readsTheSharedRefusalCodesTheWayARebaseMeansThem() {
        assertEquals(
            "“nope” isn't a branch, tag or commit here.",
            rebaseErrorCopy(gitError(SessionGitException.CODE_UNKNOWN_REF, "nope")),
        )
        assertEquals(
            "That isn't a branch, tag or commit here.",
            rebaseErrorCopy(gitError(SessionGitException.CODE_UNKNOWN_REF)),
        )
        assertEquals(
            "No rebase is in progress anymore.",
            rebaseErrorCopy(gitError(SessionGitException.CODE_NO_REBASE, "clean")),
        )
        assertEquals(
            "Local changes are in the way — stage, commit or discard them first.",
            rebaseErrorCopy(gitError(GitJobResponse.CODE_DIRTY_WORKTREE, "keep.txt")),
        )
        assertEquals(
            "A rebase or merge is already in progress.",
            rebaseErrorCopy(gitError(GitJobResponse.CODE_OPERATION_IN_PROGRESS, "merging")),
        )
        assertEquals("This branch has no commits yet.", rebaseErrorCopy(gitError(SessionGitException.CODE_NO_HEAD)))
        assertEquals(
            "The pre-rebase hook refused the rebase.",
            rebaseErrorCopy(gitError(SessionGitException.CODE_HOOK_FAILED, "nope")),
        )
        assertEquals(
            "Rebasing needs git 2.26 or newer on the agent machine.",
            rebaseErrorCopy(gitError(SessionGitException.CODE_GIT_TOO_OLD)),
        )
        assertEquals(
            "The rebase took too long and was cut off — continue or abort it.",
            rebaseErrorCopy(gitError(SessionGitException.CODE_GIT_TIMEOUT)),
        )
    }

    @Test
    fun rebaseErrorCopy_fallsBackToTheMutationCopy_forEverythingElse() {
        assertEquals(
            "Resolve the conflicts first.",
            rebaseErrorCopy(gitError(SessionGitException.CODE_UNMERGED, "unmerged paths in the index")),
        )
        assertEquals(
            "Another fetch, pull or push is still running.",
            rebaseErrorCopy(gitError(SessionGitException.CODE_JOB_RUNNING, "ab12")),
        )
        assertEquals(
            "Could not reach the agent. Check the connection and try again.",
            rebaseErrorCopy(SessionGitException.Network(IOException("down"))),
        )
    }

    @Test
    fun rebaseErrorOutput_showsGitsOutput_onlyForTheRefusalsItExplains() {
        assertEquals(
            "unstaged changes to tracked files",
            rebaseErrorOutput(gitError(GitJobResponse.CODE_DIRTY_WORKTREE, "unstaged changes to tracked files")),
        )
        assertEquals(
            "error: The pre-rebase hook refused to rebase.",
            rebaseErrorOutput(gitError(SessionGitException.CODE_HOOK_FAILED, "error: The pre-rebase hook refused to rebase.")),
        )
        assertNull(rebaseErrorOutput(gitError(SessionGitException.CODE_HOOK_FAILED, "")))
        assertNull(rebaseErrorOutput(gitError(SessionGitException.CODE_UNKNOWN_REF, "nope")))
        assertNull(rebaseErrorOutput(SessionGitException.Network(IOException("down"))))
    }
}
