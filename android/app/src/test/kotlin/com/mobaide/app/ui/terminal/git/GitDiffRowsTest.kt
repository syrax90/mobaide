package com.mobaide.app.ui.terminal.git

import com.mobaide.app.data.network.dto.GitDiffHunk
import com.mobaide.app.data.network.dto.GitDiffLine
import com.mobaide.app.data.network.dto.GitDiffResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The pure row-building half of the Diff screen ([buildDiffRows] and friends). */
class GitDiffRowsTest {

    private fun line(
        t: String,
        old: Int?,
        new: Int?,
        text: String,
        noNewline: Boolean = false,
    ): GitDiffLine = GitDiffLine(t = t, old = old, new = new, text = text, no_newline = noNewline)

    private fun hunk(
        oldStart: Int,
        oldLines: Int,
        newStart: Int,
        newLines: Int,
        heading: String = "",
        lines: List<GitDiffLine>,
    ): GitDiffHunk = GitDiffHunk(
        old_start = oldStart,
        old_lines = oldLines,
        new_start = newStart,
        new_lines = newLines,
        heading = heading,
        lines = lines,
    )

    private fun diff(
        hunks: List<GitDiffHunk> = emptyList(),
        kind: String = GitDiffResponse.KIND_TEXT,
        oldMode: String? = "100644",
        newMode: String? = "100644",
        truncated: Boolean = false,
        totalLines: Int = 0,
    ): GitDiffResponse = GitDiffResponse(
        path = "src/a.py",
        scope = GitDiffResponse.SCOPE_UNSTAGED,
        kind = kind,
        change = GitDiffResponse.CHANGE_MODIFIED,
        old_mode = oldMode,
        new_mode = newMode,
        hunks = hunks,
        truncated = truncated,
        total_lines = totalLines,
    )

    private val textHunk = hunk(
        oldStart = 6, oldLines = 4, newStart = 6, newLines = 5, heading = "class Foo:",
        lines = listOf(
            line(GitDiffLine.T_CTX, 6, 6, "def foo():"),
            line(GitDiffLine.T_DEL, 7, null, "    return x"),
            line(GitDiffLine.T_ADD, null, 7, "    y = 2"),
        ),
    )

    @Test
    fun textDiff_rendersAHunkHeaderThenItsLines() {
        val rows = buildDiffRows(diff(hunks = listOf(textHunk)))

        assertEquals(
            listOf(
                DiffRow.HunkHeader("h0", "@@ -6,4 +6,5 @@", "class Foo:"),
                DiffRow.Line("h0:l0", GitDiffLine.T_CTX, 6, 6, "def foo():"),
                DiffRow.Line("h0:l1", GitDiffLine.T_DEL, 7, null, "    return x"),
                DiffRow.Line("h0:l2", GitDiffLine.T_ADD, null, 7, "    y = 2"),
            ),
            rows,
        )
    }

    @Test
    fun rowKeys_areUniqueAcrossHunks() {
        val second = hunk(
            oldStart = 20, oldLines = 1, newStart = 21, newLines = 1,
            lines = listOf(line(GitDiffLine.T_CTX, 20, 21, "tail")),
        )
        val rows = buildDiffRows(diff(hunks = listOf(textHunk, second)))

        assertEquals(rows.size, rows.map { it.key }.toSet().size)
        assertEquals(DiffRow.HunkHeader("h1", "@@ -20,1 +21,1 @@", ""), rows[4])
        assertEquals("h1:l0", rows[5].key)
    }

    @Test
    fun theNoNewlineMarker_followsItsLine() {
        val hunk = hunk(
            oldStart = 0, oldLines = 0, newStart = 1, newLines = 2,
            lines = listOf(
                line(GitDiffLine.T_ADD, null, 1, "first"),
                line(GitDiffLine.T_ADD, null, 2, "last", noNewline = true),
            ),
        )
        val rows = buildDiffRows(diff(hunks = listOf(hunk)))

        assertEquals(
            listOf("h0", "h0:l0", "h0:l1", "h0:l1:nn"),
            rows.map { it.key },
        )
        assertTrue(rows.last() is DiffRow.NoNewline)
    }

    @Test
    fun aTruncatedDiff_endsWithTheShownAndTotalCounts() {
        val rows = buildDiffRows(diff(hunks = listOf(textHunk), truncated = true, totalLines = 12345))

        assertEquals(DiffRow.Truncated("truncated", shown = 3, total = 12345), rows.last())
        assertEquals(5, rows.size)
    }

    @Test
    fun binaryTooLargeAndMissing_collapseToASingleNotice() {
        assertEquals(
            listOf(DiffRow.Notice("notice", "Binary file — no preview")),
            buildDiffRows(diff(kind = GitDiffResponse.KIND_BINARY)),
        )
        assertEquals(
            listOf(DiffRow.Notice("notice", "File too large to preview (1 MiB limit)")),
            buildDiffRows(diff(kind = GitDiffResponse.KIND_TOO_LARGE)),
        )
        assertEquals(
            listOf(DiffRow.Notice("notice", "Nothing to show for this file")),
            buildDiffRows(diff(kind = GitDiffResponse.KIND_MISSING)),
        )
    }

    @Test
    fun aTextDiffWithoutHunks_explainsItself() {
        assertEquals(
            listOf(DiffRow.Notice("notice", "Mode changed only: 100644 → 100755")),
            buildDiffRows(diff(oldMode = "100644", newMode = "100755")),
        )
        assertEquals(
            listOf(DiffRow.Notice("notice", "No text changes")),
            buildDiffRows(diff()),
        )
    }

    @Test
    fun aSubmoduleDiff_rendersItsPointerLinesLikeText() {
        val hunk = hunk(
            oldStart = 1, oldLines = 1, newStart = 1, newLines = 1,
            lines = listOf(
                line(GitDiffLine.T_DEL, 1, null, "Subproject commit 0123"),
                line(GitDiffLine.T_ADD, null, 1, "Subproject commit 7654"),
            ),
        )
        val rows = buildDiffRows(diff(hunks = listOf(hunk), kind = GitDiffResponse.KIND_SUBMODULE))

        assertEquals(3, rows.size)
        assertEquals("Subproject commit 7654", (rows[2] as DiffRow.Line).text)
    }

    @Test
    fun renderableText_expandsTabsAndMakesCarriageReturnsVisible() {
        assertEquals("    x = 1␍", renderableText("\tx = 1\r"))
        assertEquals("a    b    c", renderableText("a\tb\tc"))
        assertEquals("plain", renderableText("plain"))
        assertEquals("", renderableText(""))
    }

    @Test
    fun lineText_isMadeRenderableAtRowBuildTime() {
        val hunk = hunk(
            oldStart = 1, oldLines = 1, newStart = 1, newLines = 1,
            lines = listOf(line(GitDiffLine.T_CTX, 1, 1, "\tx = 1\r")),
        )
        val rows = buildDiffRows(diff(hunks = listOf(hunk)))

        assertEquals("    x = 1␍", (rows[1] as DiffRow.Line).text)
    }

    @Test
    fun renderableText_cutsPastTheColumnCap_andLeavesTheCapItselfAlone() {
        val cut = renderableText("x".repeat(300_000))

        assertEquals(2_001, cut.length)
        assertTrue(cut.endsWith("…"))
        assertEquals("x".repeat(2_000), cut.dropLast(1))

        val exact = "y".repeat(2_000)
        assertEquals(exact, renderableText(exact))
    }

    @Test
    fun renderableText_expandsTabsBeforeCutting() {
        // 1 998 chars plus one tab is 1 999 chars raw, but 2 002 columns once the
        // tab is four spaces, so the cut has to land after the expansion.
        val cut = renderableText("z".repeat(1_998) + "\t")

        assertEquals(2_001, cut.length)
        assertEquals("z".repeat(1_998) + "  " + "…", cut)
    }

    @Test
    fun buildDiffRows_neverYieldsALineWiderThanTheCap() {
        val hunk = hunk(
            oldStart = 0, oldLines = 0, newStart = 1, newLines = 1,
            lines = listOf(line(GitDiffLine.T_ADD, null, 1, "m".repeat(300_000))),
        )
        val rows = buildDiffRows(diff(hunks = listOf(hunk)))

        val text = (rows[1] as DiffRow.Line).text
        assertEquals(2_001, text.length)
        assertTrue(text.endsWith("…"))
    }

    @Test
    fun gutterDigits_fitsTheLargestLineNumber_andNeverDropsBelowTwo() {
        assertEquals(2, gutterDigits(diff()))
        assertEquals(2, gutterDigits(diff(hunks = listOf(textHunk))))

        val deep = hunk(
            oldStart = 995, oldLines = 10, newStart = 1000, newLines = 7,
            lines = emptyList(),
        )
        assertEquals(4, gutterDigits(diff(hunks = listOf(deep))))

        val hundreds = hunk(oldStart = 90, oldLines = 12, newStart = 1, newLines = 1, lines = emptyList())
        assertEquals(3, gutterDigits(diff(hunks = listOf(hundreds))))
    }

    @Test
    fun widthCandidates_rankLinesByWeightedLength_andHonourTheLimit() {
        // Non-ASCII glyphs count double: "éé" outranks "abc" despite fewer chars.
        val rows = listOf(
            DiffRow.HunkHeader("h0", "@@ -1,1 +1,1 @@ this header is the longest text of all", ""),
            DiffRow.Line("h0:l0", GitDiffLine.T_CTX, 1, 1, "ab"),
            DiffRow.Line("h0:l1", GitDiffLine.T_CTX, 2, 2, "abcdef"),
            DiffRow.Line("h0:l2", GitDiffLine.T_CTX, 3, 3, "éé"),
            DiffRow.Line("h0:l3", GitDiffLine.T_CTX, 4, 4, "abc"),
            DiffRow.NoNewline("h0:l3:nn"),
        )

        assertEquals(listOf("abcdef", "éé", "abc", "ab"), widthCandidates(rows))
        assertEquals(listOf("abcdef", "éé"), widthCandidates(rows, limit = 2))
        assertTrue(widthCandidates(emptyList()).isEmpty())
    }
}
