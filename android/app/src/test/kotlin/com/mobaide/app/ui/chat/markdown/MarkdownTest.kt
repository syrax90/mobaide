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
}
