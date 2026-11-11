import XCTest
@testable import Mobaide

/// Pure derivations for the Diff screen (`GitDiffDisplay`), driven by the
/// golden `GitDiffResponse` fixtures plus hand-built responses for the shapes
/// the fixtures do not carry (too-large, mode-only, over-wide lines).
final class GitDiffDisplayTests: XCTestCase {
    private func diff(_ name: String) throws -> GitDiffResponse {
        try FixtureLoader.decodeCase(GitDiffResponse.self, path: "dto/agent/GitDiffResponse.json", name: name)
    }

    private func line(_ t: String, old: Int?, new: Int?, text: String, noNewline: Bool = false) -> GitDiffLine {
        GitDiffLine(t: t, old: old, new: new, text: text, no_newline: noNewline)
    }

    private func hunk(oldStart: Int, oldLines: Int, newStart: Int, newLines: Int, heading: String = "", lines: [GitDiffLine]) -> GitDiffHunk {
        GitDiffHunk(old_start: oldStart, old_lines: oldLines, new_start: newStart, new_lines: newLines, heading: heading, lines: lines)
    }

    private func diff(
        kind: String = GitDiffResponse.kindText,
        change: String? = GitDiffResponse.changeModified,
        oldMode: String? = "100644",
        newMode: String? = "100644",
        hunks: [GitDiffHunk] = [],
        truncated: Bool = false,
        totalLines: Int = 0,
        byteLimit: Int = 1_048_576
    ) -> GitDiffResponse {
        GitDiffResponse(
            path: "p",
            orig_path: nil,
            scope: GitDiffResponse.scopeUnstaged,
            kind: kind,
            change: change,
            old_mode: oldMode,
            new_mode: newMode,
            additions: 0,
            deletions: 0,
            hunks: hunks,
            truncated: truncated,
            total_lines: totalLines,
            line_limit: 5000,
            byte_limit: byteLimit
        )
    }

    // MARK: - rows(for:)

    func testTextDiffFlattensHunksIntoHeaderThenLines() throws {
        let rows = GitDiffDisplay.rows(for: try diff("text_modified"))

        XCTAssertEqual(rows.map(\.id), ["h0", "h0:l0", "h0:l1", "h0:l2", "h0:l3", "h0:l4", "h0:l5"])
        XCTAssertEqual(rows[0].kind, .hunkHeader(range: "@@ -6,4 +6,5 @@", heading: "class Foo:"))
        XCTAssertEqual(rows[1].kind, .line(type: "ctx", old: 6, new: 6, text: "def foo():"))
        XCTAssertEqual(rows[2].kind, .line(type: "ctx", old: 7, new: 7, text: "    x = 1␍"), "the CR is made visible")
        XCTAssertEqual(rows[3].kind, .line(type: "del", old: 8, new: nil, text: "    return x"))
        XCTAssertEqual(rows[4].kind, .line(type: "add", old: nil, new: 8, text: "    y = 2"))
    }

    func testNoNewlineMarkerFollowsItsLine() throws {
        let rows = GitDiffDisplay.rows(for: try diff("no_newline_added"))

        XCTAssertEqual(rows.map(\.id), ["h0", "h0:l0", "h0:l1", "h0:l1:nn"])
        XCTAssertEqual(rows.last?.kind, .noNewline)
    }

    func testTruncatedDiffEndsWithTheFooterAndSkipsHeaderOnlyHunks() throws {
        let rows = GitDiffDisplay.rows(for: try diff("truncated"))
        XCTAssertEqual(rows.map(\.id), ["h0", "h0:l0", "truncated"])
        XCTAssertEqual(rows.last?.kind, .truncated(shown: 1, total: 12345))

        // A hunk the byte cap emptied is overflow, not content.
        let overflow = diff(
            hunks: [
                hunk(oldStart: 1, oldLines: 1, newStart: 1, newLines: 1, lines: [line("ctx", old: 1, new: 1, text: "{")]),
                hunk(oldStart: 9, oldLines: 3, newStart: 9, newLines: 3, lines: []),
            ],
            truncated: true,
            totalLines: 500
        )
        XCTAssertEqual(GitDiffDisplay.rows(for: overflow).map(\.id), ["h0", "h0:l0", "truncated"])
    }

    func testHeaderOnlyHunkIsKeptWhenTheDiffIsComplete() {
        let complete = diff(hunks: [hunk(oldStart: 9, oldLines: 0, newStart: 9, newLines: 0, lines: [])], totalLines: 0)
        XCTAssertEqual(GitDiffDisplay.rows(for: complete).map(\.id), ["h0"])
    }

    // MARK: - renderableText(_:)

    func testRenderableTextExpandsTabsAndMarksCarriageReturns() {
        XCTAssertEqual(GitDiffDisplay.renderableText("\tif x:\r"), "    if x:␍")
        XCTAssertEqual(GitDiffDisplay.renderableText("plain"), "plain")
    }

    func testRenderableTextCutsPastTheColumnCap() {
        let cap = GitDiffDisplay.maxRenderedColumns
        let exact = String(repeating: "x", count: cap)
        XCTAssertEqual(GitDiffDisplay.renderableText(exact), exact, "the cap itself is not cut")

        let over = String(repeating: "x", count: cap + 1)
        XCTAssertEqual(GitDiffDisplay.renderableText(over), exact + "…")

        // The tab is expanded before the cut, so it counts as four columns.
        let tabbed = "\t" + String(repeating: "y", count: cap - 2)
        XCTAssertEqual(GitDiffDisplay.renderableText(tabbed), "    " + String(repeating: "y", count: cap - 4) + "…")
    }

    // MARK: - widthCandidates(_:limit:)

    private func lineRow(_ id: String, _ text: String) -> DiffRow {
        DiffRow(id: id, kind: .line(type: GitDiffLine.typeContext, old: 1, new: 1, text: text))
    }

    func testWidthCandidatesCountACJKGlyphAsTwoColumns() {
        let rows = [lineRow("l0", "abcde"), lineRow("l1", "日本語")]

        XCTAssertEqual(GitDiffDisplay.widthCandidates(rows, limit: 1), ["日本語"], "3 wide glyphs outweigh 5 ASCII ones")
    }

    // MARK: - weightedLength(_:)

    func testWeightedLengthCountsLatinGreekAndCyrillicOnceAndEveryOtherUTF16UnitTwice() {
        XCTAssertEqual(GitDiffDisplay.weightedLength(""), 0)
        XCTAssertEqual(GitDiffDisplay.weightedLength("a\tb ~"), 5)
        XCTAssertEqual(GitDiffDisplay.weightedLength("\u{E9}"), 1)
        XCTAssertEqual(GitDiffDisplay.weightedLength("\u{24F}"), 1, "the last Latin Extended-B letter")
        XCTAssertEqual(GitDiffDisplay.weightedLength("\u{250}"), 2, "IPA extensions are past Latin")
        XCTAssertEqual(GitDiffDisplay.weightedLength("e\u{301}"), 3, "a combining mark sits between the one-cell ranges")
        XCTAssertEqual(GitDiffDisplay.weightedLength("\u{36F}\u{370}"), 3, "Greek starts at U+0370")
        XCTAssertEqual(GitDiffDisplay.weightedLength("\u{4FF}\u{500}"), 3, "Cyrillic ends at U+04FF")
        XCTAssertEqual(GitDiffDisplay.weightedLength("Привет, x"), 9)
        XCTAssertEqual(GitDiffDisplay.weightedLength("日本語"), 6)
        XCTAssertEqual(GitDiffDisplay.weightedLength("✅"), 2)
        XCTAssertEqual(GitDiffDisplay.weightedLength("🙂"), 4, "a surrogate pair is two units, like Android")
    }

    // MARK: - narrowLength(_:) and narrowCandidate(_:)

    func testNarrowCandidateIsTheFirstLineWithTheMostOneCellUnitsNotTheLongest() {
        let rows = [
            DiffRow(id: "h0", kind: .hunkHeader(range: "@@ -1,4 +1,4 @@", heading: String(repeating: "h", count: 80))),
            lineRow("l0", String(repeating: "नमस्ते दुनिया ", count: 4)),
            lineRow("l1", String(repeating: "e\u{301}", count: 10)),
            lineRow("l2", "Привет, мир!"),
            lineRow("l3", "Hello, world"),
            DiffRow(id: "l3:nn", kind: .noNewline),
        ]

        XCTAssertEqual(GitDiffDisplay.narrowCandidate(rows), "Привет, мир!", "a header is never the candidate; ties go to the first line")
    }

    // MARK: - metrics(rows:gutterDigits:font:)

    func testMetricsSizeGuttersInCellsAndTheTextToTheWidestLine() {
        let narrow = GitDiffDisplay.metrics(rows: [lineRow("l0", String(repeating: "x", count: 40))], gutterDigits: 4)
        let wide = GitDiffDisplay.metrics(
            rows: [lineRow("l0", "short"), lineRow("l1", String(repeating: "x", count: 80)), lineRow("l2", "tiny")],
            gutterDigits: 2
        )

        XCTAssertGreaterThan(narrow.cell, 0)
        XCTAssertEqual(narrow.gutterWidth, narrow.cell * 4)
        XCTAssertEqual(wide.gutterWidth, wide.cell * 2)
        XCTAssertEqual(
            wide.textWidth - wide.cell,
            2 * (narrow.textWidth - narrow.cell),
            accuracy: 1,
            "a monospaced line twice as long measures twice as wide, plus one cell of slack"
        )
    }

    private func measured(_ text: String) -> CGFloat {
        (text as NSString).size(withAttributes: [.font: MobaideTypography.codeUIFont]).width
    }

    func testMetricsMeasureALongerASCIILineRankedAboveAScreenfulOfCyrillicProse() {
        let prose = String(repeating: "Привет, мир! ", count: 6)
        let url = "https://example.com/" + String(repeating: "a", count: 80)
        let rows = (0..<GitDiffDisplay.widthMeasureLimit).map { lineRow("p\($0)", prose) } + [lineRow("u", url)]
        XCTAssertEqual(GitDiffDisplay.widthCandidates(rows).first, url, "premise: Cyrillic weighs one cell, so the longer URL ranks first")
        XCTAssertGreaterThan(measured(url), measured(prose), "premise: the URL is the widest on screen")

        let metrics = GitDiffDisplay.metrics(rows: rows, gutterDigits: 2)

        XCTAssertEqual(metrics.textWidth, ceil(measured(url)) + metrics.cell)
    }

    func testMetricsPinAFullFileOfCyrillicProseToItsWidestMeasuredLineNotAnEstimate() {
        let widest = String(repeating: "ж", count: 120)
        let rows = [lineRow("l0", widest)] + (1...400).map { lineRow("l\($0)", String(repeating: "ж", count: 60 + $0 % 60)) }

        let metrics = GitDiffDisplay.metrics(rows: rows, gutterDigits: 2)

        XCTAssertEqual(metrics.textWidth, ceil(measured(widest)) + metrics.cell)
    }

    func testMetricsMeasureACyrillicLineOutrankedBySixteenHindiLines() {
        // Devanagari is estimated at two cells a unit but drawn far narrower, so
        // a screenful of it pushes the widest line out of the candidates.
        let hindi = String(repeating: "नमस्ते दुनिया ", count: 4)
        let cyrillic = String(repeating: "ж", count: 100)
        let rows = (0..<GitDiffDisplay.widthMeasureLimit).map { lineRow("h\($0)", hindi) } + [lineRow("c", cyrillic)]
        XCTAssertFalse(GitDiffDisplay.widthCandidates(rows).contains(cyrillic), "premise: sixteen Hindi lines outrank the Cyrillic one")
        XCTAssertLessThan(measured(hindi), measured(cyrillic), "premise: the Hindi lines draw narrower")

        let metrics = GitDiffDisplay.metrics(rows: rows, gutterDigits: 2)

        XCTAssertEqual(metrics.textWidth, ceil(measured(cyrillic)) + metrics.cell)
    }

    // MARK: - showsContextChoices(_:)

    func testContextChoicesShowForATextDiffWithHunksOfAFileOnBothSides() throws {
        XCTAssertTrue(GitDiffDisplay.showsContextChoices(try diff("text_modified")))
        XCTAssertTrue(GitDiffDisplay.showsContextChoices(try diff("renamed_with_heading")))
        XCTAssertTrue(GitDiffDisplay.showsContextChoices(try diff("truncated")))

        let hunks = [hunk(oldStart: 1, oldLines: 1, newStart: 1, newLines: 1, lines: [line("ctx", old: 1, new: 1, text: "a")])]
        XCTAssertTrue(GitDiffDisplay.showsContextChoices(diff(change: GitDiffResponse.changeCopied, hunks: hunks)))
    }

    func testContextChoicesHideForAddedDeletedAndTypeChangedFiles() throws {
        XCTAssertFalse(GitDiffDisplay.showsContextChoices(try diff("no_newline_added")))
        XCTAssertFalse(GitDiffDisplay.showsContextChoices(try diff("type_changed")))

        let hunks = [hunk(oldStart: 1, oldLines: 1, newStart: 0, newLines: 0, lines: [line("del", old: 1, new: nil, text: "gone")])]
        XCTAssertFalse(GitDiffDisplay.showsContextChoices(diff(change: GitDiffResponse.changeDeleted, hunks: hunks)))
    }

    func testContextChoicesHideForATextDiffWithoutHunks() {
        XCTAssertFalse(GitDiffDisplay.showsContextChoices(diff(change: GitDiffResponse.changeRenamed)), "a pure rename")
        XCTAssertFalse(GitDiffDisplay.showsContextChoices(diff(oldMode: "100644", newMode: "100755")), "a mode-only change")
    }

    func testContextChoicesStayForAnAbsentOrUnknownChange() {
        let hunks = [hunk(oldStart: 1, oldLines: 1, newStart: 1, newLines: 1, lines: [line("ctx", old: 1, new: 1, text: "a")])]
        XCTAssertTrue(GitDiffDisplay.showsContextChoices(diff(change: nil, hunks: hunks)))
        XCTAssertTrue(GitDiffDisplay.showsContextChoices(diff(change: "merged", hunks: hunks)))
    }
}
