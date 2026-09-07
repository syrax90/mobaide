package com.mobaide.app.ui.terminal.git

import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.GitUpstreamInfo
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
    ) : GitChangesUiState

    data class Unavailable(val reason: String, val cwd: String?) : GitChangesUiState
    data object AgentOutdated : GitChangesUiState
    data object SessionGone : GitChangesUiState
    data class Error(val message: String) : GitChangesUiState
}

data class GitChangesSection(
    val group: String,
    val title: String,
    val entries: List<GitStatusEntry>,
)

sealed interface GitChangesEvent {
    data class OpenDiff(
        val sessionId: String,
        val entry: GitStatusEntry,
        val scope: String,
    ) : GitChangesEvent
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
        else -> "git refused the request (${error.code})."
    }
    is SessionGitException.Network -> "Could not reach the agent. Check the connection and try again."
    is SessionGitException.AgentOutdated -> "Update your agent to view changes."
    is SessionGitException.SessionGone -> "This session has ended."
    is ApiException -> (extractDetail(error.errorBody) as? String)
        ?: "Request failed (HTTP ${error.code})."
    else -> error.message ?: "Something went wrong."
}
