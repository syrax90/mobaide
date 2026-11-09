import XCTest
@testable import Mobaide

/// The block layer of the chat Markdown subset. Inline spans stay source text
/// here; the renderer handles them.
final class MarkdownParserTests: XCTestCase {
    private typealias Item = MarkdownList.Item

    func testHeadingDropsClosingHashesAndParagraphKeepsLineBreaks() {
        XCTAssertEqual(
            MarkdownParser.parse("## Result ##\n\nfirst\nsecond"),
            [.heading(level: 2, text: "Result"), .paragraph("first\nsecond")]
        )
    }

    func testHeadingDropsAClosingHashRunOnlyAfterASpace() {
        let cases: [(String, MarkdownBlock)] = [
            ("# foo #", .heading(level: 1, text: "foo")),
            ("# foo#", .heading(level: 1, text: "foo#")),
            ("### ###", .heading(level: 3, text: "")),
            ("#", .heading(level: 1, text: "")),
            ("# foo \\#", .heading(level: 1, text: "foo \\#")),
            ("## foo ##   ", .heading(level: 2, text: "foo")),
            ("#\tfoo\t#", .heading(level: 1, text: "foo")),
            ("# foo # bar", .heading(level: 1, text: "foo # bar")),
            ("   ###### six", .heading(level: 6, text: "six")),
        ]
        for (source, block) in cases {
            XCTAssertEqual(MarkdownParser.parse(source), [block], source)
        }
        for source in ["#5 bolt", "#hashtag", "####### seven"] {
            XCTAssertEqual(MarkdownParser.parse(source), [.paragraph(source)], source)
        }
    }

    func testHeadingWithALongRunOfInnerSpacesParsesWithoutBacktracking() {
        let spaces = String(repeating: " ", count: 20_000)
        var open: [MarkdownBlock] = []
        var closed: [MarkdownBlock] = []

        let elapsed = ContinuousClock().measure {
            open = MarkdownParser.parse("# a" + spaces + "b")
            closed = MarkdownParser.parse("# a" + spaces + "#" + spaces)
        }

        XCTAssertEqual(open, [.heading(level: 1, text: "a" + spaces + "b")])
        XCTAssertEqual(closed, [.heading(level: 1, text: "a")])
        XCTAssertLessThan(elapsed, .seconds(1))
    }

    func testFencedCodeKeepsItsLanguageAndRawText() {
        XCTAssertEqual(
            MarkdownParser.parse("```python\nx = 1\n\n# not a heading\n```\nafter"),
            [.code(language: "python", text: "x = 1\n\n# not a heading"), .paragraph("after")]
        )
    }

    func testUnclosedFenceRunsToTheEnd() {
        XCTAssertEqual(
            MarkdownParser.parse("```\nline 1\nline 2"),
            [.code(language: nil, text: "line 1\nline 2")]
        )
    }

    func testCrlfLineEndings() {
        XCTAssertEqual(MarkdownParser.parse("a\r\nb\r\n"), [.paragraph("a\nb")])
    }

    func testNestedAndNumberedLists() {
        let nested = MarkdownList(ordered: false, start: 1, items: [Item(task: nil, blocks: [.paragraph("nested")])])
        XCTAssertEqual(
            MarkdownParser.parse("3. one\n4. two\n   - nested"),
            [.list(MarkdownList(ordered: true, start: 3, items: [
                Item(task: nil, blocks: [.paragraph("one")]),
                Item(task: nil, blocks: [.paragraph("two"), .list(nested)]),
            ]))]
        )
    }

    func testTaskListBoxes() {
        XCTAssertEqual(
            MarkdownParser.parse("- [x] done\n- [ ] todo\n- plain"),
            [.list(MarkdownList(ordered: false, start: 1, items: [
                Item(task: true, blocks: [.paragraph("done")]),
                Item(task: false, blocks: [.paragraph("todo")]),
                Item(task: nil, blocks: [.paragraph("plain")]),
            ]))]
        )
    }

    func testAListItemContinuesAcrossALongRunOfBlankLinesInOnePass() {
        let blanks = String(repeating: "\n", count: 20_000)
        var blocks: [MarkdownBlock] = []

        let elapsed = ContinuousClock().measure {
            blocks = MarkdownParser.parse("- a\n" + blanks + "  b\n- c")
        }

        XCTAssertEqual(blocks, [.list(MarkdownList(ordered: false, start: 1, items: [
            Item(task: nil, blocks: [.paragraph("a"), .paragraph("b")]),
            Item(task: nil, blocks: [.paragraph("c")]),
        ]))])
        XCTAssertLessThan(elapsed, .seconds(1))
    }

    func testABlankRunBeforeAnUnindentedLineEndsTheList() {
        XCTAssertEqual(
            MarkdownParser.parse("- a\n\n\n\nb"),
            [.list(MarkdownList(ordered: false, start: 1, items: [Item(task: nil, blocks: [.paragraph("a")])])), .paragraph("b")]
        )
    }

    func testEmptyListMarkerDoesNotInterruptAParagraph() {
        XCTAssertEqual(MarkdownParser.parse("first\n-"), [.paragraph("first\n-")])
        XCTAssertEqual(
            MarkdownParser.parse("first\n- item"),
            [.paragraph("first"), .list(MarkdownList(ordered: false, start: 1, items: [Item(task: nil, blocks: [.paragraph("item")])]))]
        )
    }

    func testTableWithAlignmentEscapedPipeAndShortRow() {
        XCTAssertEqual(
            MarkdownParser.parse("| File | Lines |\n|:-----|------:|\n| `a\\|b.py` | 120 |\n| only |\n\nnext"),
            [
                .table(MarkdownTable(
                    header: ["File", "Lines"],
                    alignments: [.leading, .trailing],
                    rows: [["`a|b.py`", "120"], ["only", ""]]
                )),
                .paragraph("next"),
            ]
        )
    }

    func testTableStopsWhereShortRowsWouldPadPastTheCellLimit() {
        // 1 000 columns over rows of one cell: each row adds 999 empty cells.
        let columns = 1_000
        let source = String(repeating: "|a", count: columns) + "|\n"
            + String(repeating: "|-", count: columns) + "|\n"
            + String(repeating: "x|\n", count: 50)
        let blocks = MarkdownParser.parse(source)
        XCTAssertEqual(blocks.count, 2)
        guard case .table(let table) = blocks.first, case .paragraph = blocks.last else {
            return XCTFail("expected a table, then a paragraph: \(blocks.map { "\($0)".prefix(20) })")
        }
        XCTAssertEqual(table.rows.count, 10)
    }

    func testQuoteTakesLazyContinuationLines() {
        XCTAssertEqual(
            MarkdownParser.parse("> Note: **run** it\nlazy\n\n* * *\n# Title"),
            [.quote([.paragraph("Note: **run** it\nlazy")]), .rule, .heading(level: 1, text: "Title")]
        )
    }

    func testTwoDashesAreNotARule() {
        XCTAssertEqual(MarkdownParser.parse("--"), [.paragraph("--")])
    }

    func testNestingStopsAtTheDepthLimit() {
        let blocks = MarkdownParser.parse(String(repeating: ">", count: 5000) + " deep")

        var depth = 0
        var current = blocks
        while case .quote(let inner)? = current.first {
            depth += 1
            current = inner
        }
        XCTAssertEqual(depth, MarkdownParser.maxDepth)
        guard case .paragraph(let rest)? = current.first else { return XCTFail("no innermost paragraph") }
        XCTAssertTrue(rest.hasSuffix(" deep"))
    }

    func testWhitespaceOnlyLinesBelowTheDepthLimitAreDropped() {
        let markers = String(repeating: ">", count: MarkdownParser.maxDepth)
        var expected: [MarkdownBlock] = []
        for _ in 0..<MarkdownParser.maxDepth {
            expected = [.quote(expected)]
        }

        XCTAssertEqual(MarkdownParser.parse(markers + "\n" + markers), expected)
    }
}
