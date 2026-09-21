package com.mobaide.app.ui.terminal.git

import com.mobaide.app.data.network.dto.GitConflictResponse
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.gitPathFileName

/**
 * Derivations and copy for the conflict resolver, line for line the web's
 * `sessionGitConflictDisplay.ts` and iOS `GitConflictDisplay`.
 *
 * The wire says `ours` / `theirs` (index stages 2 and 3). The UI never does:
 * during a rebase "ours" is the branch being rebased *onto*, the opposite of
 * what the word suggests, so every string names a side by its label and role.
 */
sealed interface GitConflictUiState {
    data object Loading : GitConflictUiState

    data class Ready(
        val conflict: GitConflictResponse,
        val scan: ConflictScan = ConflictScan(),
        val dirty: Boolean = false,
        /** A request of this screen is in flight; the editor is read-only meanwhile. */
        val busy: Boolean = false,
        /** The agent refused a write because the file moved on under the editor. */
        val stale: Boolean = false,
        /** "Keep my edits" was chosen: the next Apply overwrites the other change. */
        val overwriteWarning: Boolean = false,
        /** The path left the conflict state without this screen's doing. */
        val gone: Boolean = false,
    ) : GitConflictUiState {
        val editable: Boolean get() = isEditableConflict(conflict)

        // An oversized body is refused by the agent, and past a few MiB it would
        // not fit the relay's tunnel frame at all, so it is never sent.
        private val sendable: Boolean get() = editable && !scan.tooLarge && !busy && !gone
        val canApply: Boolean get() = sendable && !scan.hasMarkers
        val canSaveDraft: Boolean get() = sendable && dirty
    }

    data object AgentOutdated : GitConflictUiState

    data object SessionGone : GitConflictUiState

    data class Error(val message: String) : GitConflictUiState
}

sealed interface GitConflictEvent {
    /** The conflict is settled, or gone; the Changes list is the place to be. */
    data class Close(val message: String? = null) : GitConflictEvent

    data class ShowMessage(val text: String) : GitConflictEvent
}

data class ConflictScan(
    /** Conflict blocks still open: lines starting a `<<<<<<<` marker. */
    val conflicts: Int = 0,
    /** Any `<<<<<<<` or `>>>>>>>` line — what the agent refuses to mark resolved. */
    val hasMarkers: Boolean = false,
    /** `|||||||` sections: the original lines are in the text. */
    val hasBase: Boolean = false,
    /** More UTF-8 bytes than the agent's `byte_limit`. */
    val tooLarge: Boolean = false,
)

// The agent's marker test is `^(<{7,}|>{7,})(\s|$)` over bytes, and Python's
// bytes-mode `\s` is exactly these six characters.
private fun isMarkerBoundary(line: CharSequence, index: Int): Boolean =
    index >= line.length || line[index] in " \t\n\r\u000C\u000B"

private fun markerRun(line: CharSequence, char: Char): Boolean {
    var run = 0
    while (run < line.length && line[run] == char) run++
    return run >= 7 && isMarkerBoundary(line, run)
}

/**
 * Whether [text] is more than [byteLimit] UTF-8 bytes. UTF-8 is never shorter
 * than UTF-16 and at most three times as long, so most texts are settled by
 * `length` alone.
 */
internal fun exceedsByteLimit(text: CharSequence, byteLimit: Int): Boolean = when {
    text.length > byteLimit -> true
    text.length.toLong() * 3 <= byteLimit -> false
    else -> text.toString().encodeToByteArray().size > byteLimit
}

internal fun tooLargeToSaveNotice(byteLimit: Int) =
    "Too large to save here (over ${(byteLimit + 512) / 1024} KB)."

internal fun scanConflictText(text: CharSequence, byteLimit: Int = Int.MAX_VALUE): ConflictScan {
    var conflicts = 0
    var hasMarkers = false
    var hasBase = false
    for (line in text.lineSequenceByLf()) {
        if (line.isEmpty()) continue
        when (line[0]) {
            '<' -> if (markerRun(line, '<')) {
                conflicts++
                hasMarkers = true
            }
            '>' -> if (markerRun(line, '>')) hasMarkers = true
            '|' -> if (markerRun(line, '|')) hasBase = true
        }
    }
    return ConflictScan(conflicts, hasMarkers, hasBase, exceedsByteLimit(text, byteLimit))
}

/** Lines split on `\n` only, the way the agent iterates the file. */
private fun CharSequence.lineSequenceByLf(): Sequence<CharSequence> = sequence {
    var start = 0
    while (start <= length) {
        val end = indexOf('\n', start).let { if (it < 0) length else it }
        yield(subSequence(start, end))
        start = end + 1
    }
}

/**
 * The `[start, end)` range of the next `<<<<<<<` line after `from`, wrapping
 * to the first; null when none is left. A caret parked on a marker line moves
 * on to the next.
 */
internal fun nextConflictRange(text: CharSequence, from: Int): IntRange? {
    val starts = mutableListOf<IntRange>()
    var offset = 0
    for (line in text.lineSequenceByLf()) {
        if (line.isNotEmpty() && line[0] == '<' && markerRun(line, '<')) {
            starts += offset until offset + line.length
        }
        offset += line.length + 1
    }
    return starts.firstOrNull { it.first > from } ?: starts.firstOrNull()
}

internal fun conflictsLeftLabel(conflicts: Int, hasMarkers: Boolean): String = when {
    conflicts == 0 && hasMarkers -> "A conflict marker is left"
    conflicts == 0 -> "No conflict markers left"
    conflicts == 1 -> "1 conflict left"
    else -> "$conflicts conflicts left"
}

internal fun sideName(conflict: GitConflictResponse, side: String): String {
    val label = if (side == GitConflictResponse.SIDE_OURS) conflict.ours.label else conflict.theirs.label
    if (!label.isNullOrEmpty()) return label
    return if (side == GitConflictResponse.SIDE_OURS) "Current branch" else "Incoming change"
}

internal fun sideRole(state: String, side: String): String {
    val ours = side == GitConflictResponse.SIDE_OURS
    return when (state) {
        GitRepoInfo.STATE_REBASING ->
            if (ours) "the branch you are rebasing onto, plus your commits already replayed"
            else "your commit being replayed"
        GitRepoInfo.STATE_MERGING ->
            if (ours) "the branch you are on" else "the branch being merged in"
        else -> if (ours) "what you have checked out" else "the change being applied"
    }
}

/** What happened to the file, in the sides' own names. */
internal fun conflictCaption(conflict: GitConflictResponse): String {
    val ours = sideName(conflict, GitConflictResponse.SIDE_OURS)
    val theirs = sideName(conflict, GitConflictResponse.SIDE_THEIRS)
    return when (conflict.code) {
        "UU" -> "Both sides changed this file."
        "AA" -> "Both sides added this file."
        "DU" -> "$ours deleted this file; $theirs changed it."
        "UD" -> "$ours changed this file; $theirs deleted it."
        "DD" -> "Both sides deleted this file."
        "AU" -> "Only $ours has this file."
        "UA" -> "Only $theirs has this file."
        else -> "The two sides disagree about this file."
    }
}

/** How to read the markers: which region of a conflict block is which side. */
internal fun conflictLegend(conflict: GitConflictResponse, hasBase: Boolean): List<String> {
    val ours = "${sideName(conflict, GitConflictResponse.SIDE_OURS)} — " +
        "${sideRole(conflict.state, GitConflictResponse.SIDE_OURS)}."
    val theirs = "${sideName(conflict, GitConflictResponse.SIDE_THEIRS)} — " +
        "${sideRole(conflict.state, GitConflictResponse.SIDE_THEIRS)}."
    if (!hasBase) {
        return listOf("Between <<<<<<< and =======: $ours", "Between ======= and >>>>>>>: $theirs")
    }
    return listOf(
        "Between <<<<<<< and |||||||: $ours",
        "Between ||||||| and =======: the original lines, before either change.",
        "Between ======= and >>>>>>>: $theirs",
    )
}

internal fun isEditableConflict(conflict: GitConflictResponse): Boolean =
    conflict.kind == GitConflictResponse.KIND_TEXT && conflict.content != null

/** Why a conflict cannot be edited here; null for an editable one. */
internal fun conflictKindNotice(conflict: GitConflictResponse): String? = when (conflict.kind) {
    GitConflictResponse.KIND_TEXT -> null
    GitConflictResponse.KIND_TOO_LARGE ->
        "This file is too large to edit here (over ${(conflict.byte_limit + 512) / 1024} KB). " +
            "Pick a side, or resolve it in the terminal."
    GitConflictResponse.KIND_BINARY -> "This is a binary file — pick a side."
    GitConflictResponse.KIND_ENCODING ->
        "This file is not UTF-8 text — pick a side, or resolve it in the terminal."
    GitConflictResponse.KIND_MISSING -> "The file is not in the working tree — pick a side."
    GitConflictResponse.KIND_SYMLINK -> "This is a symbolic link — pick a side."
    GitConflictResponse.KIND_SUBMODULE -> "Submodule conflicts are resolved in the terminal."
    else -> "This conflict can't be resolved here — use the terminal."
}

private val TAKEABLE_KINDS = setOf(
    GitConflictResponse.KIND_TEXT,
    GitConflictResponse.KIND_TOO_LARGE,
    GitConflictResponse.KIND_BINARY,
    GitConflictResponse.KIND_ENCODING,
    GitConflictResponse.KIND_MISSING,
    GitConflictResponse.KIND_SYMLINK,
)

/** Whole-file picks exist for every known shape but a submodule or a directory in the way. */
internal fun canTakeSide(conflict: GitConflictResponse): Boolean = conflict.kind in TAKEABLE_KINDS

/** `git checkout --merge` needs both sides' versions to rebuild the markers. */
internal fun canStartOver(conflict: GitConflictResponse): Boolean =
    isEditableConflict(conflict) && conflict.ours.present && conflict.theirs.present

data class TakeChoice(
    val side: String,
    val label: String,
    val title: String,
    val body: String,
    val confirm: String,
    /** Removing the file is the destructive pick. */
    val danger: Boolean,
)

/**
 * The whole-file picks. Both sides present: use one or the other. Otherwise a
 * side that lacks the file means "delete" and the one that has it "keep";
 * when neither has it there is a single way out.
 */
internal fun takeChoices(conflict: GitConflictResponse): List<TakeChoice> {
    if (!canTakeSide(conflict)) return emptyList()
    val file = gitPathFileName(conflict.path)
    val oursPresent = conflict.ours.present
    val theirsPresent = conflict.theirs.present
    fun choice(side: String): TakeChoice {
        val name = sideName(conflict, side)
        val present = if (side == GitConflictResponse.SIDE_OURS) oursPresent else theirsPresent
        return when {
            oursPresent && theirsPresent -> TakeChoice(
                side = side,
                label = "Use $name for the whole file",
                title = "Use $name for the whole file?",
                body = "Every conflict in $file is settled with this side and the file is " +
                    "marked resolved. Edits made to it are discarded.",
                confirm = "Use this side",
                danger = false,
            )
            present -> TakeChoice(
                side = side,
                label = "Keep the file ($name)",
                title = "Keep $file?",
                body = "The file stays as $name has it and is marked resolved.",
                confirm = "Keep file",
                danger = false,
            )
            else -> TakeChoice(
                side = side,
                label = if (oursPresent || theirsPresent) "Delete the file ($name)" else "Delete the file",
                title = "Delete $file?",
                body = "The file is removed, as on $name, and the conflict is marked resolved.",
                confirm = "Delete file",
                danger = true,
            )
        }
    }
    if (!oursPresent && !theirsPresent) return listOf(choice(GitConflictResponse.SIDE_OURS))
    return listOf(choice(GitConflictResponse.SIDE_OURS), choice(GitConflictResponse.SIDE_THEIRS))
}

data class ConflictPrompt(val title: String, val body: String, val confirm: String = "")

internal fun startOverPrompt(path: String) = ConflictPrompt(
    title = "Start over?",
    body = "${gitPathFileName(path)} is rebuilt from both sides. Every edit made to it since " +
        "the conflict — here or in the terminal — is discarded.",
)

internal const val START_OVER_LABEL = "Start over"
internal const val START_OVER_WITH_BASE_LABEL = "Start over with original lines"

internal fun restorePrompt(path: String) = ConflictPrompt(
    title = "Restore the conflict?",
    body = "${gitPathFileName(path)} goes back to its conflicted state. " +
        "The resolution you applied is discarded.",
    confirm = "Restore conflict",
)

internal fun discardEditsPrompt(path: String) = ConflictPrompt(
    title = "Discard your edits?",
    body = "Your changes to ${gitPathFileName(path)} have not been saved.",
)

internal fun resolvedNotice(path: String) = "Resolved $path."

internal fun draftSavedNotice(path: String) = "Saved $path — the conflict is still open."

internal fun restoredNotice(path: String) = "Restored the conflict in $path."

internal fun fileChangedNotice(path: String) =
    "${gitPathFileName(path)} changed on the agent machine since you opened it."

internal const val KEEP_EDITS_WARNING = "Applying will overwrite the other change."

internal fun resolvedElsewhereNotice(path: String) = "${gitPathFileName(path)} is no longer in conflict."
