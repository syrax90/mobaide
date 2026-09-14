package com.mobaide.app.ui.terminal.git

import com.mobaide.app.data.network.dto.GitDiffResponse

const val DIFF_CONTEXT_DEFAULT = 3

/** The agent's context ceiling, at which an untruncated diff is the whole file. */
const val DIFF_CONTEXT_FULL = 5_000

data class DiffContextChoice(val lines: Int, val label: String)

val DIFF_CONTEXT_CHOICES: List<DiffContextChoice> = listOf(
    DiffContextChoice(DIFF_CONTEXT_DEFAULT, "Changes"),
    DiffContextChoice(DIFF_CONTEXT_FULL, "Full file"),
)

private val CONTEXTLESS_CHANGES = setOf(
    GitDiffResponse.CHANGE_ADDED,
    GitDiffResponse.CHANGE_DELETED,
    GitDiffResponse.CHANGE_TYPE_CHANGED,
)

/**
 * Whether the context choice can change what is shown: only a text diff with
 * hunks of a file present on both sides has unchanged lines to widen. An added
 * or deleted file is one all-add or all-del hunk, and a type change is both.
 */
internal fun showsContextChoices(diff: GitDiffResponse): Boolean =
    diff.kind == GitDiffResponse.KIND_TEXT &&
        diff.hunks.isNotEmpty() &&
        diff.change !in CONTEXTLESS_CHANGES

/**
 * Columns kept per rendered line. Every diff row is pinned to one shared
 * width, and Compose `Constraints` cannot represent a dimension of 2^18 px or
 * more — a single minified line would make layout throw. The agent caps lines
 * and bytes, never columns, so the client must.
 */
private const val MAX_RENDERED_COLUMNS = 2_000
private const val LINE_CUT_MARK = "…"

private const val TAB_WIDTH = 4

/** Candidates always measured. */
internal const val WIDTH_MEASURE_FLOOR = 8

/** Candidates measured at most. */
internal const val WIDTH_MEASURE_LIMIT = 16

/** [context] lives on every variant so the chip selection survives a failed load. */
sealed interface GitDiffUiState {
    val context: Int

    data class Loading(override val context: Int) : GitDiffUiState

    data class Ready(
        val diff: GitDiffResponse,
        val rows: List<DiffRow>,
        val gutterDigits: Int,
        /** Line texts, widest estimate first, for the shared scroll width. */
        val widthCandidates: List<String>,
        override val context: Int,
        val refreshing: Boolean = false,
        /** The line with the most one-cell chars, always measured too. */
        val narrowCandidate: String? = null,
    ) : GitDiffUiState

    data class AgentOutdated(override val context: Int) : GitDiffUiState
    data class SessionGone(override val context: Int) : GitDiffUiState
    data class Error(val message: String, override val context: Int) : GitDiffUiState
}

sealed interface DiffRow {
    val key: String

    data class HunkHeader(override val key: String, val range: String, val heading: String) : DiffRow
    data class Line(
        override val key: String,
        val type: String,
        val old: Int?,
        val new: Int?,
        val text: String,
    ) : DiffRow
    data class NoNewline(override val key: String) : DiffRow
    data class Truncated(override val key: String, val shown: Int, val total: Int) : DiffRow
    data class Notice(override val key: String, val text: String) : DiffRow
}

internal fun buildDiffRows(diff: GitDiffResponse): List<DiffRow> {
    val rows = mutableListOf<DiffRow>()
    when (diff.kind) {
        GitDiffResponse.KIND_BINARY -> rows += DiffRow.Notice("notice", "Binary file — no preview")
        GitDiffResponse.KIND_TOO_LARGE -> rows += DiffRow.Notice(
            "notice",
            "File too large to preview (${diff.byte_limit / (1024 * 1024)} MiB limit)",
        )
        GitDiffResponse.KIND_MISSING -> rows += DiffRow.Notice("notice", "Nothing to show for this file")
        else -> {
            var shown = 0
            diff.hunks.forEachIndexed { h, hunk ->
                // A hunk with no lines in a truncated diff is overflow past the cap, not content.
                if (diff.truncated && hunk.lines.isEmpty()) return@forEachIndexed
                rows += DiffRow.HunkHeader(
                    key = "h$h",
                    range = "@@ -${hunk.old_start},${hunk.old_lines} +${hunk.new_start},${hunk.new_lines} @@",
                    heading = hunk.heading,
                )
                hunk.lines.forEachIndexed { i, line ->
                    rows += DiffRow.Line("h$h:l$i", line.t, line.old, line.new, renderableText(line.text))
                    shown++
                    if (line.no_newline) rows += DiffRow.NoNewline("h$h:l$i:nn")
                }
            }
            when {
                diff.truncated -> rows += DiffRow.Truncated("truncated", shown, diff.total_lines)
                rows.isEmpty() -> rows += DiffRow.Notice("notice", emptyDiffNotice(diff))
            }
        }
    }
    return rows
}

private fun emptyDiffNotice(diff: GitDiffResponse): String {
    val old = diff.old_mode
    val new = diff.new_mode
    return if (old != null && new != null && old != new) "Mode changed only: $old → $new"
    else "No text changes"
}

/**
 * Tabs become spaces so the measured width bounds the rendered width (Android
 * lays tabs out on its own stops); a carriage return becomes a visible ␍ so a
 * CRLF file reads as one rather than hiding a control character; anything past
 * [MAX_RENDERED_COLUMNS] is cut with [LINE_CUT_MARK].
 */
internal fun renderableText(text: String): String {
    val expanded =
        if ('\t' !in text && '\r' !in text) text
        else text.replace("\t", " ".repeat(TAB_WIDTH)).replace('\r', '␍')
    return if (expanded.length <= MAX_RENDERED_COLUMNS) expanded
    else expanded.take(MAX_RENDERED_COLUMNS) + LINE_CUT_MARK
}

internal fun gutterDigits(diff: GitDiffResponse): Int {
    var max = 0
    for (hunk in diff.hunks) {
        max = maxOf(max, hunk.old_start + hunk.old_lines, hunk.new_start + hunk.new_lines)
    }
    return maxOf(2, max.toString().length)
}

/** Line texts, widest estimate first. */
internal fun widthCandidates(rows: List<DiffRow>, limit: Int = WIDTH_MEASURE_LIMIT): List<String> =
    rows.asSequence()
        .filterIsInstance<DiffRow.Line>()
        .map { it.text to weightedLength(it.text) }
        .sortedByDescending { it.second }
        .take(limit)
        .map { it.first }
        .toList()

/**
 * A line's estimated width in cells: Latin, Greek and Cyrillic, which the
 * monospace face draws one cell wide, count one; every other char counts two
 * (an emoji surrogate pair, four).
 */
internal fun weightedLength(text: String): Int {
    var width = 0
    for (c in text) width += if (c.code <= 0x024F || c.code in 0x0370..0x04FF) 1 else 2
    return width
}

/** Chars the monospace face draws exactly one cell wide: a lower bound on a line's width in cells. */
internal fun narrowLength(text: String): Int {
    var width = 0
    for (c in text) if (c.code in 0x20..0x024F || c.code in 0x0370..0x04FF) width++
    return width
}

/** The line with the most one-cell chars, or null without line rows. */
internal fun narrowCandidate(rows: List<DiffRow>): String? =
    rows.asSequence()
        .filterIsInstance<DiffRow.Line>()
        .maxByOrNull { narrowLength(it.text) }
        ?.text

/**
 * The shared pinned text width in px. The top [WIDTH_MEASURE_FLOOR] candidates
 * are always measured; later ones only while their estimate could still beat
 * the widest measured line. [narrowCandidate] is measured as well, so a widest
 * line of Latin, Greek or Cyrillic that overweighted scripts pushed out of the
 * candidates is never cut.
 */
internal fun pinnedTextWidth(
    candidates: List<String>,
    exactCell: Float,
    narrowCandidate: String? = null,
    measure: (String) -> Int,
): Int {
    var widest = 0
    for ((index, candidate) in candidates.withIndex()) {
        if (index >= WIDTH_MEASURE_FLOOR && weightedLength(candidate) * exactCell <= widest + 1f) break
        widest = maxOf(widest, measure(candidate))
    }
    return narrowCandidate?.let { maxOf(widest, measure(it)) } ?: widest
}
