package com.mobaide.app.ui.terminal.git

import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.GitCommitAuthor
import com.mobaide.app.data.network.dto.GitCommitHead
import com.mobaide.app.data.network.dto.GitCommitResponse
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.data.network.dto.GitDiscardRequest
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.GitUpstreamInfo
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.data.repository.SessionGitException
import com.mobaide.app.data.repository.extractDetail

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
        /** A mutation is in flight: every control that would start another waits. */
        val busy: Boolean = false,
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
 * is staged or discarded, a conflict is resolved in the terminal (marking it
 * resolved from a phone would stage the markers). Each gated on its
 * capability, so an older agent shows no control that would 404.
 */
data class RowActions(
    val stage: Boolean = false,
    val unstage: Boolean = false,
    val discard: Boolean = false,
) {
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
    else -> detailMessage?.let { "git refused the request ($code): $it" }
        ?: "git refused the request ($code)."
}
