package com.mobaide.app.ui.chat.markdown

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [Markdown] — the agent-output reader: blocks, inline marks, and
 * the link rules (only http/https/mailto, images become links, HTML stays text).
 */
class MarkdownTest {

    private fun text(value: String) = MdInline.Text(value)

    private fun para(vararg inlines: MdInline) = MdBlock.Paragraph(inlines.toList())

    private fun links(inlines: List<MdInline>): List<String> = inlines.flatMap { inline ->
        when (inline) {
            is MdInline.Link -> listOf(inline.url) + links(inline.children)
            is MdInline.Bold -> links(inline.children)
            is MdInline.Italic -> links(inline.children)
            is MdInline.Strike -> links(inline.children)
            else -> emptyList()
        }
    }

    @Test
    fun isSafeUrl_allowsOnlyWebAndMail() {
        listOf("https://example.com", " HTTP://A.B ", "mailto:dev@example.com").forEach {
            assertTrue(it, Markdown.isSafeUrl(it))
        }
        listOf("javascript:alert(1)", "data:text/html,hi", "file:///etc/passwd", "/relative", "").forEach {
            assertFalse(it, Markdown.isSafeUrl(it))
        }
    }

    @Test
    fun links_keepSafeTargetsAndDropUnsafeOnesToTheirLabel() {
        assertEquals(
            listOf(
                MdInline.Link("https://example.com/a", listOf(text("docs"))),
                text(" "),
                text("x"),
            ),
            Markdown.parseInline("[docs](https://example.com/a \"Title\") [x](javascript:alert(1))"),
        )
    }

    @Test
    fun image_becomesALinkLabelledWithItsAltText() {
        assertEquals(
            listOf(MdInline.Link("https://example.com/l.png", listOf(text("logo")))),
            Markdown.parseInline("![logo](https://example.com/l.png)"),
        )
    }

    @Test
    fun bareUrls_areLinkedWithoutTrailingPunctuation() {
        assertEquals(
            listOf(
                text("See "),
                MdInline.Link("https://example.com/x", listOf(text("https://example.com/x"))),
                text(". ("),
                MdInline.Link("https://e.com/a", listOf(text("https://e.com/a"))),
                text(")"),
            ),
            Markdown.parseInline("See https://example.com/x. (https://e.com/a)"),
        )
    }

    @Test
    fun linkLabel_neverHoldsANestedLink() {
        assertEquals(
            listOf(MdInline.Link("https://outer.com", listOf(text("see https://inner.com")))),
            Markdown.parseInline("[see https://inner.com](https://outer.com)"),
        )
        listOf(
            "[a <https://inner.com>](https://outer.com)",
            "[**see https://inner.com**](https://outer.com)",
        ).forEach { source ->
            assertEquals(source, listOf("https://outer.com"), links(Markdown.parseInline(source)))
        }
    }

    @Test
    fun angleBracketAutolink_linksTheUrlWithoutTheBracket() {
        assertEquals(listOf("https://example.com"), links(Markdown.parseInline("Docs: <https://example.com>.")))
    }

    @Test
    fun inline_emphasisStrikeAndCode() {
        assertEquals(
            listOf(
                MdInline.Bold(listOf(text("b"))),
                text(" "),
                MdInline.Italic(listOf(text("i"))),
                text(" "),
                MdInline.Italic(listOf(text("u"))),
                text(" "),
                MdInline.Strike(listOf(text("d"))),
                text(" "),
                MdInline.Code("a ` b"),
            ),
            Markdown.parseInline("**b** *i* _u_ ~~d~~ ``a ` b``"),
        )
    }

    @Test
    fun inline_underscoresInsideWordsAndUnclosedMarkersStayText() {
        assertEquals(listOf(text("snake_case_name and *open")), Markdown.parseInline("snake_case_name and *open"))
    }

    @Test
    fun inline_backslashEscapes() {
        assertEquals(listOf(text("*not em* [x]")), Markdown.parseInline("\\*not em\\* \\[x\\]"))
    }

    @Test
    fun plain_flattensMarksAndBreaks() {
        val inlines = Markdown.parseInline("**bold** [link](https://a.b)\n`code`")
        assertEquals("bold link\ncode", Markdown.plain(inlines))
    }

    @Test
    fun inline_intrawordUnderscoresNeverOpenEmphasis() {
        assertEquals(
            listOf(text("snake_case and "), MdInline.Italic(listOf(text("real"))), text(" emphasis")),
            Markdown.parseInline("snake_case and _real_ emphasis"),
        )
        assertEquals(listOf(text("foo__bar__ baz")), Markdown.parseInline("foo__bar__ baz"))
    }

    @Test
    fun emphasis_afterALinkWhoseTargetHoldsABacktick_stillCloses() {
        assertEquals(
            listOf(
                text("*a "),
                MdInline.Link("http://e/`", listOf(text("x"))),
                text(" "),
                MdInline.Italic(listOf(text("b"))),
                text(" c` d"),
            ),
            Markdown.parseInline("*a [x](http://e/`) *b* c` d"),
        )
    }

    @Test
    fun emphasis_afterAnImageOrAutolinkHoldingABacktick_stillCloses() {
        listOf("*a ![x](http://e/`) *b* c` d", "*a <http://e/`> *b* c` d").forEach { source ->
            assertTrue(source, MdInline.Italic(listOf(text("b"))) in Markdown.parseInline(source))
        }
    }

    @Test(timeout = 2_000)
    fun longRunsOfUnmatchedOpeners_parseWithoutRescanning() {
        listOf("*a ", "_a ", "**a ", "__a ", "~~a ", "x_y ").forEach { opener ->
            val source = opener.repeat(50_000)
            assertEquals(opener, listOf(text(source)), Markdown.parseInline(source))
        }
    }

    @Test(timeout = 2_000)
    fun longRunOfOneMarker_staysText() {
        listOf("*", "_", "~").forEach { marker ->
            val source = "a " + marker.repeat(20_000)
            assertEquals(marker, listOf(para(text(source))), Markdown.parse(source))
        }
    }

    @Test
    fun linkLabel_isCappedAt999Characters() {
        val label = "a".repeat(999)
        assertEquals(
            listOf(MdInline.Link("https://e.com", listOf(text(label)))),
            Markdown.parseInline("[$label](https://e.com)"),
        )

        val tooLong = "a".repeat(1000)
        assertEquals(
            listOf(
                text("[$tooLong]("),
                MdInline.Link("https://e.com", listOf(text("https://e.com"))),
                text(")"),
            ),
            Markdown.parseInline("[$tooLong](https://e.com)"),
        )
    }

    @Test
    fun linkDestination_of2000Characters_links() {
        val url = "https://e.com/".padEnd(2_000, 'a')
        val link = MdInline.Link(url, listOf(text("x")))
        assertEquals(listOf(link), Markdown.parseInline("[x]($url)"))
        assertEquals(listOf(link), Markdown.parseInline("[x](<$url>)"))
    }

    @Test
    fun linkDestinationOrTitlePast2048Characters_staysLiteral() {
        val url = "https://e.com/".padEnd(3_000, 'a')
        val autolink = MdInline.Link(url, listOf(text(url)))
        assertEquals(listOf(text("[x]("), autolink, text(")")), Markdown.parseInline("[x]($url)"))
        assertEquals(listOf(text("[x]("), autolink, text(")")), Markdown.parseInline("[x](<$url>)"))
        val title = "t".repeat(3_000)
        assertEquals(
            listOf(
                text("[x]("),
                MdInline.Link("https://e.com", listOf(text("https://e.com"))),
                text(" \"$title\")"),
            ),
            Markdown.parseInline("[x](https://e.com \"$title\")"),
        )
    }

    @Test
    fun linkDestinationAndTitle_shareOne2048CharacterBound() {
        val url = "https://e.com/".padEnd(1_500, 'a')
        val title = "t".repeat(1_000)
        val literal = listOf(text("[x]("), MdInline.Link(url, listOf(text(url))), text(" \"$title\")"))
        assertEquals(literal, Markdown.parseInline("[x](<$url> \"$title\")"))
        assertEquals(literal, Markdown.parseInline("[x]($url \"$title\")"))
        val shortUrl = "https://e.com/".padEnd(1_000, 'a')
        assertEquals(
            listOf(MdInline.Link(shortUrl, listOf(text("x")))),
            Markdown.parseInline("[x](<$shortUrl> \"${"t".repeat(500)}\")"),
        )
    }

    @Test
    fun bareUrl_dropsUnbalancedClosingParensAndPunctuationTogether() {
        assertEquals(
            listOf(
                text("(see "),
                MdInline.Link("https://e.com/w_(x)", listOf(text("https://e.com/w_(x)"))),
                text(")."),
            ),
            Markdown.parseInline("(see https://e.com/w_(x))."),
        )
    }

    @Test(timeout = 2_000)
    fun bareUrl_followedBy40000ClosingParens_trimsInOnePass() {
        val closes = ")".repeat(40_000)
        assertEquals(
            listOf(
                text("see "),
                MdInline.Link("https://e.com/a", listOf(text("https://e.com/a"))),
                text(closes),
            ),
            Markdown.parseInline("see https://e.com/a$closes"),
        )
    }

    @Test
    fun blocks_headingAndParagraph() {
        assertEquals(
            listOf(
                MdBlock.Heading(2, listOf(text("Result"))),
                para(text("first"), MdInline.LineBreak, text("second")),
            ),
            Markdown.parse("## Result ##\n\nfirst\nsecond"),
        )
    }

    @Test
    fun blocks_headingDropsAClosingHashRunOnlyAfterASpace() {
        listOf(
            "# foo #" to MdBlock.Heading(1, listOf(text("foo"))),
            "# foo#" to MdBlock.Heading(1, listOf(text("foo#"))),
            "### ###" to MdBlock.Heading(3, emptyList()),
            "#" to MdBlock.Heading(1, emptyList()),
            "# foo \\#" to MdBlock.Heading(1, listOf(text("foo #"))),
            "## foo ##   " to MdBlock.Heading(2, listOf(text("foo"))),
            "#\tfoo\t#" to MdBlock.Heading(1, listOf(text("foo"))),
            "# foo # bar" to MdBlock.Heading(1, listOf(text("foo # bar"))),
            "   ###### six" to MdBlock.Heading(6, listOf(text("six"))),
        ).forEach { (source, heading) ->
            assertEquals(source, listOf(heading), Markdown.parse(source))
        }
        listOf("#5 bolt", "#hashtag", "####### seven").forEach { source ->
            assertEquals(source, listOf(para(text(source))), Markdown.parse(source))
        }
    }

    @Test(timeout = 2_000)
    fun blocks_headingWith120000InnerSpaces_parsesWithoutBacktracking() {
        val spaces = " ".repeat(120_000)
        assertEquals(
            listOf(MdBlock.Heading(1, listOf(text("a${spaces}b")))),
            Markdown.parse("# a${spaces}b"),
        )
        assertEquals(
            listOf(MdBlock.Heading(1, listOf(text("a")))),
            Markdown.parse("# a$spaces#$spaces"),
        )
    }

    @Test
    fun blocks_fencedCodeKeepsItsLanguageAndRawText() {
        assertEquals(
            listOf(MdBlock.Code("python", "x = 1\n\ny = *2*"), para(text("after"))),
            Markdown.parse("```python\nx = 1\n\ny = *2*\n```\nafter"),
        )
    }

    @Test
    fun blocks_unclosedFenceRunsToTheEnd() {
        assertEquals(listOf(MdBlock.Code(null, "line 1\nline 2")), Markdown.parse("```\nline 1\nline 2"))
    }

    @Test
    fun blocks_rawHtmlStaysText() {
        assertEquals(
            listOf(para(text("<script>alert(1)</script>"))),
            Markdown.parse("<script>alert(1)</script>"),
        )
    }

    @Test
    fun blocks_nestedAndNumberedLists() {
        assertEquals(
            listOf(
                MdBlock.ListBlock(
                    ordered = true,
                    start = 3,
                    items = listOf(
                        MdListItem(null, listOf(para(text("one")))),
                        MdListItem(
                            null,
                            listOf(
                                para(text("two")),
                                MdBlock.ListBlock(false, 1, listOf(MdListItem(null, listOf(para(text("nested")))))),
                            ),
                        ),
                    ),
                ),
            ),
            Markdown.parse("3. one\n4. two\n   - nested"),
        )
    }

    @Test
    fun blocks_taskListBoxes() {
        assertEquals(
            listOf(
                MdBlock.ListBlock(
                    ordered = false,
                    start = 1,
                    items = listOf(
                        MdListItem(true, listOf(para(text("done")))),
                        MdListItem(false, listOf(para(text("todo")))),
                        MdListItem(null, listOf(para(text("plain")))),
                    ),
                ),
            ),
            Markdown.parse("- [x] done\n- [ ] todo\n- plain"),
        )
    }

    @Test
    fun blocks_bareTaskBoxIsAnEmptyTaskItem() {
        assertEquals(
            listOf(
                MdBlock.ListBlock(
                    ordered = false,
                    start = 1,
                    items = listOf(
                        MdListItem(false, emptyList()),
                        MdListItem(true, emptyList()),
                        MdListItem(false, listOf(para(text("next")))),
                    ),
                ),
            ),
            Markdown.parse("- [ ]\n- [X]\n- [ ] next"),
        )
    }

    @Test
    fun blocks_tableWithAlignmentAndAPipeInCode() {
        assertEquals(
            listOf(
                MdBlock.Table(
                    header = listOf(listOf(text("File")), listOf(text("Lines"))),
                    aligns = listOf(MdAlign.Start, MdAlign.End),
                    rows = listOf(listOf(listOf(MdInline.Code("a|b.py")), listOf(text("120")))),
                ),
                para(text("next")),
            ),
            Markdown.parse("| File | Lines |\n|:-----|------:|\n| `a|b.py` | 120 |\n\nnext"),
        )
    }

    @Test
    fun blocks_separatorCellsAreDashesWithOptionalColonsAtTheEnds() {
        assertEquals(
            listOf(
                MdBlock.Table(
                    header = listOf(listOf(text("a")), listOf(text("b")), listOf(text("c"))),
                    aligns = listOf(MdAlign.Center, MdAlign.Start, MdAlign.End),
                    rows = emptyList(),
                ),
            ),
            Markdown.parse("| a | b | c |\n| :-: | --- | -: |"),
        )
        listOf("-:- | ---", ": | ---", ":: | ---", "-- - | ---").forEach { separator ->
            assertEquals(
                separator,
                listOf(para(text("a | b"), MdInline.LineBreak, text(separator))),
                Markdown.parse("a | b\n$separator"),
            )
        }
    }

    @Test
    fun blocks_separatorRowOf20000Cells_parsesWithoutStackOverflow() {
        val columns = 20_000
        assertEquals(
            listOf(MdBlock.Table(List(columns) { listOf(text("a")) }, List(columns) { MdAlign.Start }, emptyList())),
            Markdown.parse("|a".repeat(columns) + "|\n" + "|-".repeat(columns) + "|"),
        )
        assertEquals(
            listOf(para(text("a|b"), MdInline.LineBreak, text("|-".repeat(columns) + "|"))),
            Markdown.parse("a|b\n" + "|-".repeat(columns) + "|"),
        )
    }

    @Test
    fun blocks_quoteRuleAndHeadingEndAParagraph() {
        assertEquals(
            listOf(
                MdBlock.Quote(listOf(para(text("Note: "), MdInline.Bold(listOf(text("run"))), text(" it")))),
                para(text("plain")),
                MdBlock.Rule,
                MdBlock.Heading(1, listOf(text("Title"))),
            ),
            Markdown.parse("> Note: **run** it\nplain\n---\n# Title"),
        )
    }

    @Test
    fun blocks_crlfIsALineBreak() {
        assertEquals(listOf(para(text("a"), MdInline.LineBreak, text("b"))), Markdown.parse("a\r\nb"))
    }

    private fun bulletOf(vararg inlines: MdInline): MdBlock =
        MdBlock.ListBlock(false, 1, listOf(MdListItem(null, listOf(para(*inlines)))))

    @Test
    fun blocks_listFenceAndQuoteLinesHoldingALineSeparator_stillParse() {
        listOf("U+2028" to "\u2028", "U+2029" to "\u2029", "U+0085" to "\u0085").forEach { (name, separator) ->
            assertEquals(name, listOf(bulletOf(text("a${separator}b"))), Markdown.parse("- a${separator}b"))
            assertEquals(name, listOf(MdBlock.Code("py", "code")), Markdown.parse("```py x${separator}y\ncode\n```"))
            assertEquals(
                name,
                listOf(MdBlock.Quote(listOf(para(text("a${separator}b"))))),
                Markdown.parse("> a${separator}b"),
            )
        }
    }

    @Test(timeout = 2_000)
    fun blocks_longListOrFenceLineHoldingALineSeparator_parsesWithoutBacktracking() {
        assertEquals(listOf(bulletOf(text("a\u2028b"))), Markdown.parse("- " + " ".repeat(64_000) + "a\u2028b"))
        assertEquals(listOf(MdBlock.Code("py", "")), Markdown.parse("`".repeat(64_000) + "py x\u2028y"))
    }

    private fun nested(levels: Int, wrap: (List<MdBlock>) -> MdBlock, inner: List<MdBlock>): List<MdBlock> =
        (1..levels).fold(inner) { blocks, _ -> listOf(wrap(blocks)) }

    private val quote: (List<MdBlock>) -> MdBlock = { MdBlock.Quote(it) }

    private val bullet: (List<MdBlock>) -> MdBlock = { MdBlock.ListBlock(false, 1, listOf(MdListItem(null, it))) }

    @Test
    fun blocks_quotesNestTwelveLevels_andDeeperMarkersStayOneParagraph() {
        assertEquals(nested(12, quote, listOf(para(text("deep")))), Markdown.parse(">".repeat(12) + " deep"))
        assertEquals(
            nested(12, quote, listOf(para(text(">".repeat(9_988) + " deep")))),
            Markdown.parse(">".repeat(10_000) + " deep"),
        )
    }

    @Test
    fun blocks_listsNestTwelveLevels_andDeeperMarkersStayOneParagraph() {
        assertEquals(nested(12, bullet, listOf(para(text("deep")))), Markdown.parse("- ".repeat(12) + "deep"))
        assertEquals(
            nested(12, bullet, listOf(para(text("- ".repeat(9_988) + "deep")))),
            Markdown.parse("- ".repeat(10_000) + "deep"),
        )
    }
}
