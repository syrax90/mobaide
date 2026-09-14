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

    func testTwoHunksKeepTheirOwnHeaders() throws {
        let rows = GitDiffDisplay.rows(for: try diff("type_changed"))

        XCTAssertEqual(rows.map(\.id), ["h0", "h0:l0", "h0:l0:nn", "h1", "h1:l0"])
        XCTAssertEqual(rows[3].kind, .hunkHeader(range: "@@ -0,0 +1,1 @@", heading: ""))
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

    func testNoticesForBinaryTooLargeAndMissing() throws {
        XCTAssertEqual(GitDiffDisplay.rows(for: try diff("binary")).map(\.kind), [.notice("Binary file — no preview")])
        XCTAssertEqual(GitDiffDisplay.rows(for: try diff("missing")).map(\.kind), [.notice("Nothing to show for this file")])
        XCTAssertEqual(
            GitDiffDisplay.rows(for: diff(kind: GitDiffResponse.kindTooLarge)).map(\.kind),
            [.notice("File too large to preview (1 MiB limit)")]
        )
    }

    func testEmptyTextDiffReportsModeOnlyOrNoChanges() {
        XCTAssertEqual(
            GitDiffDisplay.rows(for: diff(oldMode: "100644", newMode: "100755")).map(\.kind),
            [.notice("Mode changed only: 100644 → 100755")]
        )
        XCTAssertEqual(
            GitDiffDisplay.rows(for: diff(oldMode: "100644", newMode: "100644")).map(\.kind),
            [.notice("No text changes")]
        )
        XCTAssertEqual(
            GitDiffDisplay.rows(for: diff(oldMode: nil, newMode: "100644")).map(\.kind),
            [.notice("No text changes")],
            "a missing side is not a mode change"
        )
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

    // MARK: - gutterDigits(for:)

    func testGutterDigitsCoverTheHighestLineNumberWithAFloorOfTwo() throws {
        XCTAssertEqual(GitDiffDisplay.gutterDigits(for: try diff("text_modified")), 2)
        XCTAssertEqual(GitDiffDisplay.gutterDigits(for: try diff("binary")), 2, "no hunks still reserves two columns")
        XCTAssertEqual(
            GitDiffDisplay.gutterDigits(for: diff(hunks: [hunk(oldStart: 995, oldLines: 10, newStart: 995, newLines: 3, lines: [])])),
            4
        )
    }

    // MARK: - notices and chips

    func testTruncatedNoticeNeverClaimsAnExactTotal() {
        XCTAssertEqual(GitDiffDisplay.truncatedNotice(shown: 1, total: 12345), "Showing the first 1 of at least 12345 lines")
        XCTAssertEqual(GitDiffDisplay.truncatedNotice(shown: 5000, total: 5000), "Truncated after 5000 lines — the full diff is longer")
    }

    func testHunkHeaderTextOmitsAnEmptyHeading() {
        XCTAssertEqual(GitDiffDisplay.hunkHeaderText(range: "@@ -1,2 +1,3 @@", heading: ""), "@@ -1,2 +1,3 @@")
        XCTAssertEqual(GitDiffDisplay.hunkHeaderText(range: "@@ -1,2 +1,3 @@", heading: "class Foo:"), "@@ -1,2 +1,3 @@  class Foo:")
    }

    func testChangeToneAndLabel() {
        XCTAssertEqual(GitDiffDisplay.changeTone(GitDiffResponse.changeAdded), .ok)
        XCTAssertEqual(GitDiffDisplay.changeTone(GitDiffResponse.changeDeleted), .danger)
        XCTAssertEqual(GitDiffDisplay.changeTone(GitDiffResponse.changeRenamed), .accent)
        XCTAssertEqual(GitDiffDisplay.changeTone(GitDiffResponse.changeCopied), .accent)
        XCTAssertEqual(GitDiffDisplay.changeTone(GitDiffResponse.changeModified), .warning)
        XCTAssertEqual(GitDiffDisplay.changeTone(GitDiffResponse.changeTypeChanged), .warning)
        XCTAssertEqual(GitDiffDisplay.changeLabel(GitDiffResponse.changeTypeChanged), "type changed")
    }

    func testLineToneAndMarkerPerType() {
        XCTAssertEqual(GitDiffDisplay.lineTone(GitDiffLine.typeAdd), .ok)
        XCTAssertEqual(GitDiffDisplay.lineTone(GitDiffLine.typeDelete), .danger)
        XCTAssertNil(GitDiffDisplay.lineTone(GitDiffLine.typeContext))
        XCTAssertEqual(GitDiffDisplay.marker(GitDiffLine.typeAdd), "+")
        XCTAssertEqual(GitDiffDisplay.marker(GitDiffLine.typeDelete), "-")
        XCTAssertEqual(GitDiffDisplay.marker(GitDiffLine.typeContext), " ")
    }

    func testModeChangeAndCountsVisibility() throws {
        XCTAssertEqual(GitDiffDisplay.modeChange(try diff("type_changed")), "120000 → 100644")
        XCTAssertNil(GitDiffDisplay.modeChange(try diff("text_modified")))
        XCTAssertNil(GitDiffDisplay.modeChange(try diff("no_newline_added")), "an added file has no old mode to compare")

        XCTAssertTrue(GitDiffDisplay.showsCounts(try diff("text_modified")))
        XCTAssertTrue(GitDiffDisplay.showsCounts(try diff("submodule")))
        XCTAssertFalse(GitDiffDisplay.showsCounts(try diff("binary")))
        XCTAssertFalse(GitDiffDisplay.showsCounts(try diff("missing")))
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

    func testContextChoicesHideForEveryNonTextKind() throws {
        XCTAssertFalse(GitDiffDisplay.showsContextChoices(try diff("submodule")), "a submodule's pointer hunk has no context to widen")
        XCTAssertFalse(GitDiffDisplay.showsContextChoices(try diff("binary")))
        XCTAssertFalse(GitDiffDisplay.showsContextChoices(try diff("missing")))
        XCTAssertFalse(GitDiffDisplay.showsContextChoices(diff(kind: GitDiffResponse.kindTooLarge)))
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
