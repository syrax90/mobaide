package com.mobaide.app.ui.terminal.git

import com.mobaide.app.data.network.dto.GitDiffResponse

const val DIFF_CONTEXT_DEFAULT = 3
val DIFF_CONTEXT_CHOICES: List<Int> = listOf(3, 10, 25)

/**
 * Columns kept per rendered line. Every diff row is pinned to one shared
 * width, and Compose `Constraints` cannot represent a dimension of 2^18 px or
 * more — a single minified line would make layout throw. The agent caps lines
 * and bytes, never columns, so the client must.
 */
private const val MAX_RENDERED_COLUMNS = 2_000
private const val LINE_CUT_MARK = "…"

private const val TAB_WIDTH = 4
private const val WIDTH_CANDIDATES = 8

/** [context] lives on every variant so the chip selection survives a failed load. */
sealed interface GitDiffUiState {
    val context: Int

    data class Loading(override val context: Int) : GitDiffUiState

    data class Ready(
        val diff: GitDiffResponse,
        val rows: List<DiffRow>,
        val gutterDigits: Int,
        /** The lines most likely to be the widest, for the shared scroll width. */
        val widthCandidates: List<String>,
        override val context: Int,
        val refreshing: Boolean = false,
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

internal fun widthCandidates(rows: List<DiffRow>, limit: Int = WIDTH_CANDIDATES): List<String> =
    rows.asSequence()
        .filterIsInstance<DiffRow.Line>()
        .map { it.text to weightedLength(it.text) }
        .sortedByDescending { it.second }
        .take(limit)
        .map { it.first }
        .toList()

/** Non-ASCII glyphs usually come from a wider fallback face; count them double. */
private fun weightedLength(text: String): Int {
    var width = 0
    for (c in text) width += if (c.code > 0x7F) 2 else 1
    return width
}
