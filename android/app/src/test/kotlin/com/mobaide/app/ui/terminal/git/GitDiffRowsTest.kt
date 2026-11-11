package com.mobaide.app.ui.terminal.git

import com.mobaide.app.data.network.dto.GitDiffHunk
import com.mobaide.app.data.network.dto.GitDiffLine
import com.mobaide.app.data.network.dto.GitDiffResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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
        change: String? = GitDiffResponse.CHANGE_MODIFIED,
        oldMode: String? = "100644",
        newMode: String? = "100644",
        truncated: Boolean = false,
        totalLines: Int = 0,
    ): GitDiffResponse = GitDiffResponse(
        path = "src/a.py",
        scope = GitDiffResponse.SCOPE_UNSTAGED,
        kind = kind,
        change = change,
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
    fun aTruncatedDiff_dropsHeaderOnlyHunks() {
        val empty1 = hunk(oldStart = 40, oldLines = 2, newStart = 41, newLines = 2, lines = emptyList())
        val empty2 = hunk(oldStart = 90, oldLines = 1, newStart = 92, newLines = 1, lines = emptyList())
        val rows = buildDiffRows(
            diff(hunks = listOf(textHunk, empty1, empty2), truncated = true, totalLines = 9),
        )

        assertEquals(
            listOf(
                DiffRow.HunkHeader("h0", "@@ -6,4 +6,5 @@", "class Foo:"),
                DiffRow.Line("h0:l0", GitDiffLine.T_CTX, 6, 6, "def foo():"),
                DiffRow.Line("h0:l1", GitDiffLine.T_DEL, 7, null, "    return x"),
                DiffRow.Line("h0:l2", GitDiffLine.T_ADD, null, 7, "    y = 2"),
                DiffRow.Truncated("truncated", shown = 3, total = 9),
            ),
            rows,
        )
    }

    @Test
    fun anEmptyHunk_keepsItsHeader_whenNothingWasTruncated() {
        val empty = hunk(oldStart = 40, oldLines = 0, newStart = 41, newLines = 0, lines = emptyList())
        val rows = buildDiffRows(diff(hunks = listOf(textHunk, empty)))

        assertEquals(DiffRow.HunkHeader("h1", "@@ -40,0 +41,0 @@", ""), rows.last())
        assertEquals(5, rows.size)
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
    fun showsContextChoices_isOff_forAnAddedDeletedOrTypeChangedFile() {
        for (change in listOf(
            GitDiffResponse.CHANGE_ADDED,
            GitDiffResponse.CHANGE_DELETED,
            GitDiffResponse.CHANGE_TYPE_CHANGED,
        )) {
            assertFalse(change, showsContextChoices(diff(hunks = listOf(textHunk), change = change)))
        }
    }

    @Test
    fun showsContextChoices_isOff_forEveryNonTextKind_evenOneWithHunks() {
        for (kind in listOf(
            GitDiffResponse.KIND_BINARY,
            GitDiffResponse.KIND_SUBMODULE,
            GitDiffResponse.KIND_TOO_LARGE,
            GitDiffResponse.KIND_MISSING,
        )) {
            assertFalse(kind, showsContextChoices(diff(hunks = listOf(textHunk), kind = kind)))
        }
    }

    @Test
    fun showsContextChoices_isOff_forATextDiffWithoutHunks() {
        assertFalse(showsContextChoices(diff(change = GitDiffResponse.CHANGE_RENAMED)))
        assertFalse(showsContextChoices(diff(oldMode = "100644", newMode = "100755")))
    }

    @Test
    fun showsContextChoices_staysOn_whenTheChangeIsAbsentOrUnknown() {
        assertTrue(showsContextChoices(diff(hunks = listOf(textHunk), change = null)))
        assertTrue(showsContextChoices(diff(hunks = listOf(textHunk), change = "merged")))
    }

    @Test
    fun renderableText_expandsTabsAndMakesCarriageReturnsVisible() {
        assertEquals("    x = 1␍", renderableText("\tx = 1\r"))
        assertEquals("a    b    c", renderableText("a\tb\tc"))
        assertEquals("plain", renderableText("plain"))
        assertEquals("", renderableText(""))
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
        // CJK glyphs count double: "日日" outranks "abc" despite fewer chars.
        val rows = listOf(
            DiffRow.HunkHeader("h0", "@@ -1,1 +1,1 @@ this header is the longest text of all", ""),
            DiffRow.Line("h0:l0", GitDiffLine.T_CTX, 1, 1, "ab"),
            DiffRow.Line("h0:l1", GitDiffLine.T_CTX, 2, 2, "abcdef"),
            DiffRow.Line("h0:l2", GitDiffLine.T_CTX, 3, 3, "日日"),
            DiffRow.Line("h0:l3", GitDiffLine.T_CTX, 4, 4, "abc"),
            DiffRow.NoNewline("h0:l3:nn"),
        )

        assertEquals(listOf("abcdef", "日日", "abc", "ab"), widthCandidates(rows))
        assertEquals(listOf("abcdef", "日日"), widthCandidates(rows, limit = 2))
        assertTrue(widthCandidates(emptyList()).isEmpty())
    }

    private fun lineRows(texts: List<String>): List<DiffRow> =
        texts.mapIndexed { i, text -> DiffRow.Line("h0:l$i", GitDiffLine.T_CTX, i + 1, i + 1, text) }

    @Test
    fun widthCandidates_byDefault_holdTheMeasureLimit_widestEstimateFirst() {
        val rows = lineRows((1..300).map { "x".repeat(it) })

        assertEquals((300 downTo 301 - WIDTH_MEASURE_LIMIT).map { "x".repeat(it) }, widthCandidates(rows))
    }

    @Test
    fun widthCandidates_rankALongerAsciiLine_aboveAScreenfulOfCyrillicProse() {
        val prose = "Привет, мир! ".repeat(6)
        val url = "https://example.com/" + "a".repeat(80)
        val rows = lineRows(List(WIDTH_MEASURE_LIMIT) { prose } + url)

        assertEquals(listOf(url) + List(WIDTH_MEASURE_LIMIT - 1) { prose }, widthCandidates(rows))
    }

    @Test
    fun weightedLength_countsLatinGreekAndCyrillicOnce_andEveryOtherCharTwice() {
        assertEquals(0, weightedLength(""))
        assertEquals(5, weightedLength("a\tb ~"))
        assertEquals(1, weightedLength("\u00E9"))
        assertEquals("the last Latin Extended-B letter", 1, weightedLength("\u024F"))
        assertEquals("IPA extensions are past Latin", 2, weightedLength("\u0250"))
        assertEquals("a combining mark sits between the one-cell ranges", 3, weightedLength("e\u0301"))
        assertEquals("Greek starts at U+0370", 3, weightedLength("\u036F\u0370"))
        assertEquals("Cyrillic ends at U+04FF", 3, weightedLength("\u04FF\u0500"))
        assertEquals(9, weightedLength("Привет, x"))
        assertEquals(6, weightedLength("日本語"))
        assertEquals(2, weightedLength("✅"))
        // A surrogate pair is two chars, so an emoji is estimated at four cells.
        assertEquals(4, weightedLength("🙂"))
    }

    @Test
    fun narrowLength_countsOnlyTheCharsDrawnOneCellWide() {
        assertEquals(0, narrowLength(""))
        assertEquals(4, narrowLength("ab ~"))
        assertEquals("a control char draws no cell", 2, narrowLength("a\tb"))
        assertEquals("U+0020 is the first char counted", 1, narrowLength("\u001F\u0020"))
        assertEquals("Latin ends at U+024F", 1, narrowLength("\u024F\u0250"))
        assertEquals("a combining mark adds no cell", 1, narrowLength("e\u0301"))
        assertEquals("Greek starts at U+0370", 1, narrowLength("\u036F\u0370"))
        assertEquals("Cyrillic ends at U+04FF", 1, narrowLength("\u04FF\u0500"))
        assertEquals(9, narrowLength("Привет, x"))
        assertEquals(4, narrowLength("αβγδ"))
        assertEquals(0, narrowLength("日本語"))
        assertEquals(0, narrowLength("नमस्ते"))
        assertEquals(0, narrowLength("مرحبا"))
        assertEquals(0, narrowLength("สวัสดี"))
        assertEquals(0, narrowLength("✅🙂"))
    }

    @Test
    fun narrowCandidate_isTheFirstLineWithTheMostOneCellChars_notTheLongest() {
        val rows = listOf(
            DiffRow.HunkHeader("h0", "@@ -1,4 +1,4 @@", "a heading longer than every line is never the candidate"),
            DiffRow.Line("h0:l0", GitDiffLine.T_CTX, 1, 1, "नमस्ते दुनिया ".repeat(4)),
            DiffRow.Line("h0:l1", GitDiffLine.T_CTX, 2, 2, "e\u0301".repeat(10)),
            DiffRow.Line("h0:l2", GitDiffLine.T_CTX, 3, 3, "Привет, мир!"),
            DiffRow.Line("h0:l3", GitDiffLine.T_CTX, 4, 4, "Hello, world"),
            DiffRow.NoNewline("h0:l3:nn"),
        )

        assertEquals("Привет, мир!", narrowCandidate(rows))
    }

    @Test
    fun pinnedTextWidth_measuresTheWholeFloor_evenACandidateEstimatedNarrower() {
        val wide = "wide" + "y".repeat(6)
        val candidates = listOf("x".repeat(100)) + List(WIDTH_MEASURE_FLOOR - 2) { "y".repeat(10) } + wide
        val measure = { text: String -> if (text == wide) 5_000 else text.length * 10 }

        assertEquals(5_000, pinnedTextWidth(candidates, exactCell = 10f, measure = measure))
        assertEquals(0, pinnedTextWidth(emptyList(), exactCell = 10f, measure = measure))
    }

    @Test
    fun pinnedTextWidth_pastTheFloor_stopsAtAnEstimateThatCannotBeatTheWidest() {
        val floor = List(WIDTH_MEASURE_FLOOR) { "x".repeat(100) }
        val measure = { text: String -> if (text.startsWith("wide")) 5_000 else text.length * 10 }

        val shorter = floor + ("wide" + "y".repeat(86))
        assertEquals(1_000, pinnedTextWidth(shorter, exactCell = 10f, measure = measure))

        // A cell measured a hair wide must not turn an equal estimate into a measurement.
        val tied = floor + ("wide" + "y".repeat(96))
        assertEquals(1_000, pinnedTextWidth(tied, exactCell = 10.004f, measure = measure))
    }

    @Test
    fun pinnedTextWidth_pastTheFloor_keepsMeasuringWhileAnEstimateCouldBeatTheWidest() {
        // CJK is estimated at two cells a glyph but drawn narrower.
        val candidates = List(WIDTH_MEASURE_FLOOR) { "日".repeat(50) } + "x".repeat(96) + "x".repeat(60)
        val measure = { text: String -> if (text.startsWith("日")) 700 else text.length * 10 }

        assertEquals(960, pinnedTextWidth(candidates, exactCell = 10f, measure = measure))
    }

    @Test
    fun pinnedTextWidth_ofTheDefaultCandidates_neverMeasuresPastTheLimit() {
        // Every estimate beats every measurement, so only the limit ends the scan;
        // the lines ranked past it draw far wider but are never measured.
        val texts = (120 downTo 101).map { "x".repeat(it) }
        val measure = { text: String -> if (text.length > 120 - WIDTH_MEASURE_LIMIT) 500 else 9_000 }

        assertEquals(500, pinnedTextWidth(widthCandidates(lineRows(texts)), exactCell = 10f, measure = measure))
    }

    @Test
    fun pinnedTextWidth_measuresTheNarrowCandidate_whenOverweightedLinesPushItPastTheLimit() {
        // Devanagari is estimated at two cells a char but drawn far narrower.
        val hindi = "नमस्ते दुनिया ".repeat(4)
        val cyrillic = "ж".repeat(100)
        val rows = lineRows(List(WIDTH_MEASURE_LIMIT) { hindi } + cyrillic)
        val candidates = widthCandidates(rows)
        assertFalse("premise: the Cyrillic line is not a candidate", cyrillic in candidates)
        val measure = { text: String -> if (text == hindi) 360 else text.length * 10 }
        assertEquals("premise: without it the line is cut", 360, pinnedTextWidth(candidates, exactCell = 10f, measure = measure))

        assertEquals(
            1_000,
            pinnedTextWidth(candidates, exactCell = 10f, narrowCandidate = narrowCandidate(rows), measure = measure),
        )
    }

    @Test
    fun pinnedTextWidth_neverNarrowsToTheNarrowCandidate() {
        val cjk = "日".repeat(50)
        val ascii = "x".repeat(40)
        val measure = { text: String -> if (text == cjk) 800 else text.length * 10 }

        assertEquals(800, pinnedTextWidth(listOf(cjk, ascii), exactCell = 10f, narrowCandidate = ascii, measure = measure))
    }
}
