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
}
