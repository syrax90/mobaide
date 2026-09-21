package com.mobaide.app.ui.terminal.git

import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.GitCommitAuthor
import com.mobaide.app.data.network.dto.GitCommitHead
import com.mobaide.app.data.network.dto.GitCommitResponse
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.data.network.dto.GitDiscardRequest
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.data.network.dto.GitJobResponse
import com.mobaide.app.data.network.dto.GitRebaseResponse
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.GitUpstreamInfo
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.data.repository.SessionGitException
import com.mobaide.app.data.repository.extractDetail
import com.mobaide.app.ui.projects.git.RefNameOrder

sealed interface GitChangesUiState {
    data object Loading : GitChangesUiState

    data class Ready(
        val status: GitStatusResponse,
        val repo: GitRepoInfo,
        val sections: List<GitChangesSection>,
        val refreshing: Boolean = false,
        /** The last poll failed but [status] is still worth showing. */
        val staleError: String? = null,
        /** Which mutating controls the agent advertises; none for an older agent. */
        val capabilities: GitCapabilities = GitCapabilities.NONE,
        /** Row keys ticked for the selection toolbar. */
        val selectedKeys: Set<String> = emptySet(),
        /** Selection mode entered from the top bar while nothing is ticked yet. */
        val selecting: Boolean = false,
        /** A mutation is in flight, or a fetch / pull / push owns the repository:
         *  every control that would write the staged set waits. */
        val busy: Boolean = false,
        /** The fetch / pull / push this screen follows, until its result is dismissed. */
        val job: GitJobBanner? = null,
    ) : GitChangesUiState {
        val inSelectionMode: Boolean get() = selecting || selectedKeys.isNotEmpty()
        val stagedCount: Int get() = status.counts?.staged ?: 0
        /** Whether any shown row has a Stage / Unstage / Discard to offer. */
        val hasActionableRows: Boolean
            get() = sections.any { section ->
                section.entries.any { rowActions(it, capabilities).any }
            }
    }

    data class Unavailable(val reason: String, val cwd: String?) : GitChangesUiState
    data object AgentOutdated : GitChangesUiState
    data object SessionGone : GitChangesUiState
    data class Error(val message: String) : GitChangesUiState
}

data class GitChangesSection(
    val group: String,
    val title: String,
    val entries: List<GitStatusEntry>,
) {
    /**
     * `LazyColumn` keys, unique even when the agent's lossy decoding of two
     * POSIX filenames with different invalid bytes yields the same
     * [GitStatusEntry.path]: `group:occurrence:path`, with the occurrence
     * ahead of the path so no filename can forge another entry's key.
     */
    val keys: List<String> = uniqueKeys(group, entries)
}

sealed interface GitChangesEvent {
    data class OpenDiff(
        val sessionId: String,
        val entry: GitStatusEntry,
        val scope: String,
    ) : GitChangesEvent

    /** A transient outcome for the snackbar: a commit's short SHA, a refused mutation. */
    data class ShowMessage(val text: String) : GitChangesEvent
}

/**
 * What a row can do: a staged entry is unstaged, a modified or untracked one
 * is staged or discarded, a conflict is marked resolved once fixed — only on
 * an agent that refuses to stage leftover markers (`resolve`). Each gated on
 * its capability, so an older agent shows no control that would 404.
 */
data class RowActions(
    val stage: Boolean = false,
    val unstage: Boolean = false,
    val discard: Boolean = false,
    /** Stage a conflicted file once it is fixed; one file at a time, never from the selection toolbar. */
    val resolve: Boolean = false,
) {
    /** Whether the selection toolbar has anything for this row; [resolve] is per row only. */
    val any: Boolean get() = stage || unstage || discard

    companion object {
        val NONE = RowActions()
    }
}

internal fun rowActions(entry: GitStatusEntry, capabilities: GitCapabilities): RowActions =
    when (entry.group) {
        GitStatusEntry.GROUP_STAGED -> RowActions(unstage = capabilities.stage)
        GitStatusEntry.GROUP_UNSTAGED, GitStatusEntry.GROUP_UNTRACKED ->
            RowActions(stage = capabilities.stage, discard = capabilities.discard)
        GitStatusEntry.GROUP_CONFLICTS ->
            RowActions(resolve = capabilities.stage && capabilities.resolve)
        else -> RowActions.NONE
    }

/** The discard route's scope for an entry; conflicts never reach it. */
internal fun discardScopeFor(entry: GitStatusEntry): String =
    if (entry.group == GitStatusEntry.GROUP_UNTRACKED) GitDiscardRequest.SCOPE_UNTRACKED
    else GitDiscardRequest.SCOPE_UNSTAGED

/**
 * The paths a request names for these entries: a rename needs both its sides
 * (`git reset` on the new name alone leaves the deletion staged).
 */
internal fun pathsFor(entries: List<GitStatusEntry>): List<String> =
    entries.flatMap { listOfNotNull(it.path, it.orig_path) }.distinct()

/** The selected entries each toolbar action applies to; a button is enabled when its list is non-empty. */
data class SelectionSubsets(
    val stage: List<GitStatusEntry> = emptyList(),
    val unstage: List<GitStatusEntry> = emptyList(),
    val discard: List<GitStatusEntry> = emptyList(),
)

internal fun selectionSubsets(
    sections: List<GitChangesSection>,
    selectedKeys: Set<String>,
    capabilities: GitCapabilities,
): SelectionSubsets {
    val stage = mutableListOf<GitStatusEntry>()
    val unstage = mutableListOf<GitStatusEntry>()
    val discard = mutableListOf<GitStatusEntry>()
    for (section in sections) {
        section.entries.forEachIndexed { index, entry ->
            if (section.keys[index] !in selectedKeys) return@forEachIndexed
            val actions = rowActions(entry, capabilities)
            if (actions.stage) stage += entry
            if (actions.unstage) unstage += entry
            if (actions.discard) discard += entry
        }
    }
    return SelectionSubsets(stage, unstage, discard)
}

/** A section header's whole-section action; none for Conflicts and unknown groups. */
data class SectionAction(val label: String, val stage: Boolean)

internal fun sectionAction(section: GitChangesSection, capabilities: GitCapabilities): SectionAction? {
    if (!capabilities.stage) return null
    return when (section.group) {
        GitStatusEntry.GROUP_STAGED -> SectionAction("Unstage all", stage = false)
        GitStatusEntry.GROUP_UNSTAGED, GitStatusEntry.GROUP_UNTRACKED ->
            SectionAction("Stage all", stage = true)
        else -> null
    }
}

/** The confirmation Discard always goes through; never one tap. */
data class DiscardPrompt(val entries: List<GitStatusEntry>) {
    val title: String
    val body: String

    init {
        val only = entries.singleOrNull()
        if (only != null) {
            val untracked = only.group == GitStatusEntry.GROUP_UNTRACKED
            title = if (untracked) "Delete untracked file?" else "Discard changes?"
            body = if (untracked) {
                "${only.path} is not tracked by git and will be deleted. This cannot be undone."
            } else {
                "Changes to ${only.path} will be thrown away. This cannot be undone."
            }
        } else {
            title = "Discard ${entries.size} files?"
            body = "Their working-tree changes will be thrown away and untracked ones deleted. " +
                "This cannot be undone."
        }
    }
}

sealed interface CommitContextState {
    data object Loading : CommitContextState

    /** [author] null: git cannot say who commits; [head] null: nothing to amend. */
    data class Ready(val author: GitCommitAuthor?, val head: GitCommitHead?) : CommitContextState

    data class Failed(val message: String) : CommitContextState
}

data class CommitError(val message: String, val output: String?)

data class CommitSheetState(
    val context: CommitContextState = CommitContextState.Loading,
    val committing: Boolean = false,
    val error: CommitError? = null,
)

/** The host-side fix shown with `identity_required`, verbatim for copying. */
internal val IDENTITY_FIX_COMMANDS: List<String> = listOf(
    "git config --global user.name \"Your Name\"",
    "git config --global user.email \"you@example.com\"",
)

/** `subject\n\nbody` — the shape `git commit -F` reads. */
internal fun composeCommitMessage(summary: String, description: String): String {
    val subject = summary.trim()
    val body = description.trim()
    return if (body.isEmpty()) subject else "$subject\n\n$body"
}

internal fun commitButtonLabel(stagedCount: Int, amend: Boolean): String = when {
    amend -> "Amend commit"
    stagedCount == 1 -> "Commit 1 file"
    else -> "Commit $stagedCount files"
}

internal fun commitNotice(result: GitCommitResponse): String =
    "Committed ${result.short_oid} · ${result.subject}"

internal fun authorLine(author: GitCommitAuthor): String =
    "Committing as ${author.name} <${author.email}>"

/**
 * Copy for a failed stage / unstage / discard / commit. One code reads
 * differently from the polls: the mutating routes need git 2.26, the reads
 * 2.15, and the agent answers `git_too_old` for both.
 */
internal fun mutationErrorCopy(error: Throwable): String =
    if (error is SessionGitException.GitError && error.code == SessionGitException.CODE_GIT_TOO_OLD) {
        "Staging needs git 2.26 or newer on the agent machine."
    } else {
        sessionGitErrorCopy(error)
    }

enum class StatusBadgeTone { Added, Deleted, Modified, Renamed, Conflict, Untracked }

data class StatusBadge(val label: String, val tone: StatusBadgeTone)

private val SECTION_ORDER = listOf(
    GitStatusEntry.GROUP_CONFLICTS to "Conflicts",
    GitStatusEntry.GROUP_STAGED to "Staged",
    GitStatusEntry.GROUP_UNSTAGED to "Changes",
    GitStatusEntry.GROUP_UNTRACKED to "Untracked",
)

internal fun sectionsFor(status: GitStatusResponse): List<GitChangesSection> {
    val byGroup = status.entries.groupBy { it.group }
    val known = SECTION_ORDER.mapNotNull { (group, title) ->
        byGroup[group]?.let { GitChangesSection(group, title, it) }
    }
    val other = byGroup.keys
        .filterNot { group -> SECTION_ORDER.any { it.first == group } }
        .sorted()
        .map { group -> GitChangesSection(group, group.replace('_', ' '), byGroup.getValue(group)) }
    return known + other
}

private fun uniqueKeys(group: String, entries: List<GitStatusEntry>): List<String> {
    val seen = mutableMapOf<String, Int>()
    return entries.map { entry ->
        val occurrence = (seen[entry.path] ?: 0) + 1
        seen[entry.path] = occurrence
        "$group:$occurrence:${entry.path}"
    }
}

/** Conflicted files are diffed against HEAD, which the agent serves as `unstaged`. */
internal fun diffScopeFor(entry: GitStatusEntry): String = when (entry.group) {
    GitStatusEntry.GROUP_STAGED -> GitDiffResponse.SCOPE_STAGED
    GitStatusEntry.GROUP_UNTRACKED -> GitDiffResponse.SCOPE_UNTRACKED
    else -> GitDiffResponse.SCOPE_UNSTAGED
}

internal fun badgeFor(entry: GitStatusEntry): StatusBadge = when (entry.group) {
    GitStatusEntry.GROUP_CONFLICTS -> StatusBadge(entry.code.ifEmpty { "U" }, StatusBadgeTone.Conflict)
    GitStatusEntry.GROUP_UNTRACKED -> StatusBadge("?", StatusBadgeTone.Untracked)
    else -> when (entry.code) {
        "A" -> StatusBadge("A", StatusBadgeTone.Added)
        "D" -> StatusBadge("D", StatusBadgeTone.Deleted)
        "R" -> StatusBadge("R", StatusBadgeTone.Renamed)
        "C" -> StatusBadge("C", StatusBadgeTone.Renamed)
        else -> StatusBadge(entry.code.ifEmpty { "M" }, StatusBadgeTone.Modified)
    }
}

internal fun headLabel(repo: GitRepoInfo): String {
    val head = repo.head
    return when {
        head.initial -> "No commits yet"
        head.detached -> "detached @ ${head.short_oid ?: "HEAD"}"
        else -> head.branch ?: head.short_oid ?: "HEAD"
    }
}

internal fun upstreamLabel(upstream: GitUpstreamInfo?): String? {
    if (upstream == null) return null
    val ahead = upstream.ahead
    val behind = upstream.behind
    if (ahead == null || behind == null) return "${upstream.ref} · upstream gone"
    return "↑$ahead ↓$behind · ${upstream.ref}"
}

internal fun repoStateLabel(state: String): String? = when (state) {
    GitRepoInfo.STATE_CLEAN -> null
    GitRepoInfo.STATE_CHERRY_PICKING -> "cherry-picking"
    else -> state.replace('_', ' ')
}

/**
 * Counts come from the full parse, but when the agent's byte cap hit before
 * the entry cap they are a lower bound too — so the shown figure is what
 * actually arrived and the total is never claimed exact.
 */
internal fun truncatedNotice(status: GitStatusResponse): String {
    val shown = status.entries.size
    val total = status.counts?.total
    return if (total != null && total > shown) "Showing the first $shown of at least $total entries."
    else "List cut short after $shown entries."
}

internal fun unavailableCopy(reason: String): Pair<String, String> = when (reason) {
    GitStatusResponse.UNAVAILABLE_NOT_A_REPO ->
        "Not a git repository" to "The session's folder is not inside a git repository."
    GitStatusResponse.UNAVAILABLE_BARE_REPOSITORY ->
        "Bare repository" to "The session runs inside a bare repository, which has no working tree to show."
    GitStatusResponse.UNAVAILABLE_CWD_MISSING ->
        "Folder not found" to "The session's folder no longer exists on the agent host."
    GitStatusResponse.UNAVAILABLE_UNSAFE_DIRECTORY ->
        "Unsafe directory" to "Git refuses this folder because another user owns it. " +
            "Allow it with `git config --global --add safe.directory <path>` on the agent host."
    GitStatusResponse.UNAVAILABLE_GIT_TOO_OLD ->
        "Git is too old" to "The agent host needs git 2.15 or newer."
    else ->
        "Changes unavailable" to "The agent could not read this repository ($reason)."
}

private val UNAVAILABLE_REASONS = setOf(
    GitStatusResponse.UNAVAILABLE_NOT_A_REPO,
    GitStatusResponse.UNAVAILABLE_BARE_REPOSITORY,
    GitStatusResponse.UNAVAILABLE_CWD_MISSING,
    GitStatusResponse.UNAVAILABLE_UNSAFE_DIRECTORY,
    GitStatusResponse.UNAVAILABLE_GIT_TOO_OLD,
)

internal fun sessionGitErrorCopy(error: Throwable): String = when (error) {
    is SessionGitException.GitError -> when (error.code) {
        SessionGitException.CODE_GIT_UNAVAILABLE -> "git is not installed on the agent host."
        SessionGitException.CODE_GIT_TIMEOUT -> "git took too long to answer. Try again."
        SessionGitException.CODE_GIT_ERROR ->
            error.detailMessage?.let { "git failed: $it" } ?: "git failed unexpectedly."
        in UNAVAILABLE_REASONS -> unavailableCopy(error.code).second
        else -> refusalCopy(error.code, error.detailMessage)
    }
    is SessionGitException.Network -> "Could not reach the agent. Check the connection and try again."
    is SessionGitException.AgentOutdated -> "Update your agent to view changes."
    is SessionGitException.SessionGone -> "This session has ended."
    is ApiException -> (extractDetail(error.errorBody) as? String)
        ?: "Request failed (HTTP ${error.code})."
    else -> error.message ?: "Something went wrong."
}

/** The mutating routes' `409` vocabulary, one line each; the same strings as iOS and web. */
internal fun refusalCopy(code: String, detailMessage: String?): String = when (code) {
    SessionGitException.CODE_IDENTITY_REQUIRED -> "Git doesn't know who you are on the agent machine."
    SessionGitException.CODE_UNMERGED -> "Resolve the conflicts first."
    SessionGitException.CODE_NOTHING_TO_COMMIT -> "Nothing is staged."
    SessionGitException.CODE_NO_HEAD -> "There is no commit to amend yet."
    SessionGitException.CODE_HOOK_FAILED -> "A commit hook rejected the commit."
    SessionGitException.CODE_INDEX_LOCKED -> "Another git process is running — try again."
    SessionGitException.CODE_PATHSPEC_MISMATCH -> "That file changed under you — refreshed."
    SessionGitException.CODE_EMPTY_MESSAGE -> "Enter a commit summary."
    SessionGitException.CODE_JOB_RUNNING -> "Another fetch, pull or push is still running."
    SessionGitException.CODE_JOB_NOT_FOUND -> JOB_LOST_COPY
    SessionGitException.CODE_CONFLICT_MARKERS ->
        "${detailMessage ?: "The file"} still has conflict markers — finish resolving it first."
    else -> detailMessage?.let { "git refused the request ($code): $it" }
        ?: "git refused the request ($code)."
}

/** How long a successful job's banner stays before it clears itself. */
const val JOB_RESULT_DISMISS_MS = 6_000L

internal const val JOB_LOST_COPY = "Lost track of the job — refresh to see the result."

/** One of the Sync sheet's three actions and whether it can run right now. */
data class SyncRow(val op: String, val label: String, val detail: String, val enabled: Boolean)

/**
 * The Sync sheet's rows from the same status the header renders; the copy is
 * shared with iOS `GitChangesDisplay.syncRows` and web `syncRows`. A row exists
 * only when the agent advertises its capability; Fetch is always possible,
 * Pull needs something to pull, Push something to push — or a branch with no
 * upstream yet, which it publishes.
 */
internal fun syncRows(repo: GitRepoInfo?, capabilities: GitCapabilities): List<SyncRow> {
    val rows = mutableListOf<SyncRow>()
    val head = repo?.head
    val upstream = repo?.upstream
    val ref = upstream?.ref
    val blocked = when {
        head == null -> "Repository unavailable"
        head.initial -> "No commits yet"
        head.detached -> "Detached HEAD"
        else -> null
    }
    if (capabilities.fetch) {
        rows += SyncRow(GitJobResponse.OP_FETCH, "Fetch", "Update remote-tracking branches", enabled = true)
    }
    if (capabilities.pull) {
        val behind = upstream?.behind
        rows += when {
            blocked != null -> SyncRow(GitJobResponse.OP_PULL, "Pull", blocked, enabled = false)
            upstream == null -> SyncRow(GitJobResponse.OP_PULL, "Pull", "No upstream", enabled = false)
            behind == null -> SyncRow(GitJobResponse.OP_PULL, "Pull", "Upstream gone", enabled = false)
            behind == 0 -> SyncRow(GitJobResponse.OP_PULL, "Pull", "Nothing to pull", enabled = false)
            else -> SyncRow(
                GitJobResponse.OP_PULL,
                "Pull",
                "Fast-forward $behind ${if (behind == 1) "commit" else "commits"} from ${ref ?: "upstream"}",
                enabled = true,
            )
        }
    }
    if (capabilities.push) {
        val ahead = upstream?.ahead
        rows += when {
            blocked != null -> SyncRow(GitJobResponse.OP_PUSH, "Push", blocked, enabled = false)
            upstream == null ->
                SyncRow(GitJobResponse.OP_PUSH, "Publish branch", "Push and set the upstream", enabled = true)
            ahead == null ->
                SyncRow(GitJobResponse.OP_PUSH, "Push", "Recreate ${ref ?: "the upstream"}", enabled = true)
            ahead == 0 -> SyncRow(GitJobResponse.OP_PUSH, "Push", "Nothing to push", enabled = false)
            else -> SyncRow(
                GitJobResponse.OP_PUSH,
                "Push",
                "$ahead ${if (ahead == 1) "commit" else "commits"} to ${ref ?: "upstream"}",
                enabled = true,
            )
        }
    }
    return rows
}

internal fun jobTitle(op: String): String = when (op) {
    GitJobResponse.OP_FETCH -> "Fetching…"
    GitJobResponse.OP_PULL -> "Pulling…"
    else -> "Pushing…"
}

internal fun jobDoneTitle(op: String, ok: Boolean): String = when (op) {
    GitJobResponse.OP_FETCH -> if (ok) "Fetched" else "Fetch failed"
    GitJobResponse.OP_PULL -> if (ok) "Pulled" else "Pull failed"
    else -> if (ok) "Pushed" else "Push failed"
}

/**
 * One line per job `error_code`, identical on the three clients. `message` is
 * git's own explanation and is rendered beside it in monospace, so only an
 * unknown code falls back to it here.
 */
internal fun jobErrorCopy(code: String?, message: String?): String = when (code) {
    GitJobResponse.CODE_CANCELLED -> "Cancelled."
    SessionGitException.CODE_GIT_TIMEOUT -> "git took too long to answer. Try again."
    GitJobResponse.CODE_AUTH_REQUIRED ->
        "No credentials for this remote. Configure a credential helper or SSH agent on the agent host."
    GitJobResponse.CODE_NETWORK -> "Couldn't reach the remote."
    GitJobResponse.CODE_REMOTE_NOT_FOUND -> "The remote doesn't exist."
    GitJobResponse.CODE_REJECTED -> "The remote rejected the push."
    GitJobResponse.CODE_NOT_FAST_FORWARD -> "Your branch is behind the remote."
    GitJobResponse.CODE_CONFLICTS -> "Stopped on conflicts — resolve them in the terminal."
    GitJobResponse.CODE_DIRTY_WORKTREE -> "Commit or stash your changes first."
    SessionGitException.CODE_UNMERGED -> "Resolve the conflicts first."
    GitJobResponse.CODE_OPERATION_IN_PROGRESS -> "A rebase or merge is already in progress."
    GitJobResponse.CODE_NO_UPSTREAM -> "This branch has no upstream."
    GitJobResponse.CODE_NO_REMOTE -> "This repository has no remote."
    GitJobResponse.CODE_DETACHED_HEAD -> "Detached HEAD — check out a branch first."
    SessionGitException.CODE_NO_HEAD -> "No commits yet."
    SessionGitException.CODE_HOOK_FAILED -> "A pre-push hook rejected the push."
    SessionGitException.CODE_INDEX_LOCKED -> "Another git process is running — try again."
    else -> message?.takeIf { it.isNotEmpty() } ?: "git failed."
}

/** The banner's one line: the title plus git's summary, or the title plus the failure copy. */
internal fun jobNotice(job: GitJobResponse): String {
    if (job.running) return jobTitle(job.op)
    val ok = job.ok == true
    val title = jobDoneTitle(job.op, ok)
    if (ok) return job.message?.takeIf { it.isNotEmpty() }?.let { "$title · $it" } ?: title
    return "$title: ${jobErrorCopy(job.error_code, job.message)}"
}

/** Whether the failure's `message` adds anything the copy did not already say. */
internal fun showsJobOutput(job: GitJobResponse): Boolean {
    if (job.ok != false) return false
    val message = job.message.orEmpty()
    return message.isNotEmpty() && jobErrorCopy(job.error_code, job.message) != message
}

enum class SyncFollowUp { PullRebase, PullMerge, ForcePush }

/**
 * What a failed job can be retried as: a pull that could not fast-forward as
 * a rebase or a merge, a rejected push as a push with lease. Each rewrites
 * history somewhere, so every one is confirmed first. A refused publish
 * installs no upstream and a pruned one cannot be pulled until the next
 * fetch, so — like the Pull row — the pulls are dropped when the status says
 * so, never when it is unavailable.
 */
internal fun jobFollowUps(job: GitJobResponse, repo: GitRepoInfo? = null): List<SyncFollowUp> {
    if (job.running || job.ok != false) return emptyList()
    val pulls = if (repo != null && repo.upstream?.behind == null) {
        emptyList()
    } else {
        listOf(SyncFollowUp.PullRebase, SyncFollowUp.PullMerge)
    }
    // Behind the remote: integrate first, whichever way the user prefers; a
    // push may also mean a deliberate rewrite, so the lease push stays offered.
    return when {
        job.error_code == GitJobResponse.CODE_NOT_FAST_FORWARD ->
            if (job.op == GitJobResponse.OP_PUSH) pulls + SyncFollowUp.ForcePush else pulls
        job.op == GitJobResponse.OP_PUSH && job.error_code == GitJobResponse.CODE_REJECTED ->
            listOf(SyncFollowUp.ForcePush)
        else -> emptyList()
    }
}

internal fun followUpLabel(followUp: SyncFollowUp): String = when (followUp) {
    SyncFollowUp.PullRebase -> "Pull with rebase…"
    SyncFollowUp.PullMerge -> "Pull with merge…"
    SyncFollowUp.ForcePush -> "Force push (with lease)…"
}

/** The confirmation every follow-up goes through. */
data class SyncPrompt(val title: String, val body: String, val confirm: String)

internal fun followUpPrompt(followUp: SyncFollowUp, repo: GitRepoInfo?): SyncPrompt {
    val ref = repo?.upstream?.ref ?: "the upstream"
    val branch = repo?.head?.branch ?: "your branch"
    return when (followUp) {
        SyncFollowUp.PullRebase ->
            SyncPrompt("Pull with rebase?", "Replays your commits on top of $ref.", "Rebase")
        SyncFollowUp.PullMerge ->
            SyncPrompt("Pull with merge?", "Merges $ref into $branch.", "Merge")
        SyncFollowUp.ForcePush -> SyncPrompt(
            "Force push?",
            "Overwrites $ref with your branch. Proceeds only if the remote hasn't moved since your last fetch.",
            "Force push",
        )
    }
}

/**
 * The job this screen follows: [job] is the latest snapshot (null until the
 * first one lands), [lost] that the agent no longer knows the id.
 */
data class GitJobBanner(val jobId: String, val job: GitJobResponse?, val lost: Boolean = false) {
    val running: Boolean get() = !lost && (job == null || job.running)
    val failed: Boolean get() = lost || job?.ok == false
    val text: String
        get() = when {
            lost -> JOB_LOST_COPY
            job == null -> "Syncing…"
            else -> jobNotice(job)
        }
}

data class SyncSheetState(
    /** The start request is in flight — the one moment the sheet cannot be dismissed. */
    val starting: Boolean = false,
    val error: String? = null,
    /** A follow-up waiting for its confirmation. */
    val pendingFollowUp: SyncFollowUp? = null,
)

/** One branch the rebase sheet offers. */
data class RebaseTarget(val value: String, val label: String)

/**
 * The rebase sheet's picker: local branches, then remote-tracking ones, each
 * A→Z, from the project Git screen's rows. The session's own branch is left out by name
 * — the rows' `is_current` is the project root's checkout, which for a session
 * in a linked worktree is usually the very branch it wants to rebase onto.
 */
internal fun rebaseTargets(items: List<GitItem>, currentBranch: String?): List<RebaseTarget> {
    fun usable(item: GitItem) = item.branch != null && item.branch != currentBranch
    val byName = compareBy(RefNameOrder) { item: GitItem -> item.branch.orEmpty() }
    val local = items
        .filter { it.kind == GitItem.KIND_BRANCH_ONLY || it.kind == GitItem.KIND_BRANCH_WITH_WORKTREE }
        .filter(::usable)
        .sortedWith(byName)
        .map { RebaseTarget("refs/heads/${it.branch}", it.branch.orEmpty()) }
    val remote = items
        .filter { it.kind == GitItem.KIND_REMOTE_BRANCH }
        .filter(::usable)
        .sortedWith(byName)
        .map { RebaseTarget("refs/remotes/${it.branch}", "${it.branch} (remote)") }
    return local + remote
}

/**
 * A picked target's name for copy: the picker sends fully qualified refs so a
 * tag of the same name cannot shadow the branch, but a notice reads the short one.
 */
internal fun shortRefName(ref: String): String =
    ref.removePrefix("refs/heads/").takeIf { it != ref } ?: ref.removePrefix("refs/remotes/")

/**
 * Why a rebase cannot start from this status, or null. Untracked files do not
 * block: git refuses only the ones a pick would overwrite.
 */
internal fun rebaseBlocker(status: GitStatusResponse?): String? {
    val repo = status?.repo ?: return "Repository unavailable."
    val counts = status.counts
    return when {
        repo.state != GitRepoInfo.STATE_CLEAN -> "A rebase or merge is already in progress."
        repo.head.initial -> "This branch has no commits yet."
        counts != null && counts.conflicts + counts.staged + counts.unstaged > 0 ->
            "Commit or discard your changes first."
        else -> null
    }
}

/** The branch a rebase acts on, for copy: the one being rebased, else HEAD's. */
internal fun rebasedBranch(repo: GitRepoInfo?): String =
    repo?.rebase?.branch ?: repo?.head?.branch ?: "the branch"

/**
 * The Changes screen's banner while the repository is rebasing. [actionable]
 * is false when `repo.rebase` is null — a `git am` session, which reports
 * `rebasing` too and refuses Continue / Skip / Abort, or an agent that predates
 * the rebase routes.
 */
data class RebaseBanner(val title: String, val progress: String?, val hint: String, val actionable: Boolean)

internal fun rebaseBanner(repo: GitRepoInfo?, conflicts: Int): RebaseBanner? {
    if (repo == null || repo.state != GitRepoInfo.STATE_REBASING) return null
    val info = repo.rebase
    val branch = info?.branch
    val onto = info?.onto_ref ?: info?.onto_short_oid
    val title = when {
        branch != null && onto != null -> "Rebasing $branch onto $onto"
        branch != null -> "Rebasing $branch"
        onto != null -> "Rebasing onto $onto"
        else -> "Rebase in progress"
    }
    val parts = mutableListOf<String>()
    info?.stopped_on?.let { parts += "Stopped at ${it.short_oid} ${it.subject}" }
    val step = info?.step
    val total = info?.total
    if (step != null && total != null) parts += "$step/$total"
    val actionable = info != null
    val hint = when {
        !actionable -> "Continue, skip or abort it in the terminal."
        conflicts > 0 ->
            "$conflicts ${if (conflicts == 1) "conflict" else "conflicts"} — fix the files, then Mark resolved."
        else -> "No conflicts left — Continue when ready."
    }
    return RebaseBanner(title, parts.takeIf { it.isNotEmpty() }?.joinToString(" · "), hint, actionable)
}

/** The two rebase steps that go through a confirmation. */
enum class RebasePromptStep { Skip, Abort }

internal fun rebaseStepPrompt(step: RebasePromptStep, repo: GitRepoInfo?): SyncPrompt {
    val branch = rebasedBranch(repo)
    return when (step) {
        RebasePromptStep.Skip -> {
            val stopped = repo?.rebase?.stopped_on
            val commit = stopped?.let { "${it.short_oid} “${it.subject}”" }
                ?: "The commit the rebase stopped at"
            SyncPrompt("Skip this commit?", "$commit will be left out of $branch.", "Skip commit")
        }
        RebasePromptStep.Abort -> SyncPrompt(
            "Abort the rebase?",
            "$branch goes back to where it was before the rebase. " +
                "Conflict resolutions made so far are discarded.",
            "Abort rebase",
        )
    }
}

/**
 * The message a finished rebase call leaves. [onto] is what the user picked,
 * known only for a start; Continue and Skip finish without it.
 */
internal fun rebaseOutcomeNotice(result: GitRebaseResponse, branch: String, onto: String?): String {
    val ontoName = onto?.let(::shortRefName)
    return when (result.outcome) {
        GitRebaseResponse.OUTCOME_REBASED ->
            if (ontoName == null) "Rebase finished." else "Rebased $branch onto $ontoName."
        GitRebaseResponse.OUTCOME_UP_TO_DATE ->
            "$branch is already up to date with ${ontoName ?: "that branch"}."
        GitRebaseResponse.OUTCOME_ABORTED -> "Rebase aborted — $branch is back where it started."
        GitRebaseResponse.OUTCOME_STOPPED -> result.message?.takeIf { it.isNotEmpty() }
            ?.let { "Rebase stopped: $it" }
            ?: result.rebase?.stopped_on?.subject
                ?.let { "Rebase stopped at “$it” — resolve the conflicts, then Continue." }
            ?: "Rebase stopped — resolve the conflicts, then Continue."
        else -> "Rebase: ${result.outcome}"
    }
}

/**
 * Copy for a failed rebase call: the rebase-specific readings of the shared
 * refusal codes, then the mutations' copy.
 */
internal fun rebaseErrorCopy(error: Throwable): String {
    if (error is SessionGitException.GitError) {
        when (error.code) {
            SessionGitException.CODE_UNKNOWN_REF -> return error.detailMessage?.takeIf { it.isNotEmpty() }
                ?.let { "“$it” isn't a branch, tag or commit here." }
                ?: "That isn't a branch, tag or commit here."
            SessionGitException.CODE_NO_REBASE -> return "No rebase is in progress anymore."
            GitJobResponse.CODE_DIRTY_WORKTREE ->
                return "Local changes are in the way — stage, commit or discard them first."
            GitJobResponse.CODE_OPERATION_IN_PROGRESS -> return "A rebase or merge is already in progress."
            SessionGitException.CODE_NO_HEAD -> return "This branch has no commits yet."
            SessionGitException.CODE_HOOK_FAILED -> return "The pre-rebase hook refused the rebase."
            SessionGitException.CODE_GIT_TOO_OLD -> return "Rebasing needs git 2.26 or newer on the agent machine."
            SessionGitException.CODE_GIT_TIMEOUT ->
                return "The rebase took too long and was cut off — continue or abort it."
        }
    }
    return mutationErrorCopy(error)
}

/** git's own output worth showing under the copy: which files, or what the hook said. */
internal fun rebaseErrorOutput(error: Throwable): String? {
    if (error !is SessionGitException.GitError) return null
    if (error.code != GitJobResponse.CODE_DIRTY_WORKTREE && error.code != SessionGitException.CODE_HOOK_FAILED) {
        return null
    }
    return error.detailMessage?.takeIf { it.isNotEmpty() }
}

/** Where the rebase sheet's picker stands: no project types the ref instead. */
sealed interface RebaseTargetsState {
    data object Loading : RebaseTargetsState
    data class Ready(val items: List<GitItem>) : RebaseTargetsState
    /** No project, or the list failed to load: the sheet asks for the ref as text. */
    data object Typed : RebaseTargetsState
}

data class RebaseSheetState(
    val targets: RebaseTargetsState = RebaseTargetsState.Typed,
    val starting: Boolean = false,
    val error: CommitError? = null,
)
