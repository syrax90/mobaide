package com.mobaide.app.ui.terminal.git

import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.GitCommitAuthor
import com.mobaide.app.data.network.dto.GitCommitHead
import com.mobaide.app.data.network.dto.GitCommitResponse
import com.mobaide.app.data.network.dto.GitDiscardRequest
import com.mobaide.app.data.network.dto.GitStatusCounts
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.data.repository.SessionGitException
import org.junit.Assert.assertEquals
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
    fun rowActions_gateEachActionOnItsOwnCapability() {
        val discardOnly = GitCapabilities(stage = false, discard = true, commit = true)
        val stageOnly = GitCapabilities(stage = true, discard = false, commit = false)

        assertEquals(RowActions(discard = true), rowActions(modified, discardOnly))
        assertEquals(RowActions.NONE, rowActions(rename, discardOnly))
        assertEquals(RowActions(stage = true), rowActions(modified, stageOnly))
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
