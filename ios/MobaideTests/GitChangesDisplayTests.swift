import XCTest
@testable import Mobaide

/// Pure derivations for the session-scoped Changes screen (`GitChangesDisplay`
/// and the `GitStatusEntry` path helpers), driven by the golden
/// `GitStatusResponse` fixtures where a case exists and by hand-built entries
/// where the fixtures have no such shape.
final class GitChangesDisplayTests: XCTestCase {
    private func status(_ name: String) throws -> GitStatusResponse {
        try FixtureLoader.decodeCase(GitStatusResponse.self, path: "dto/agent/GitStatusResponse.json", name: name)
    }

    private func entry(group: String, path: String, code: String = "M", origPath: String? = nil) -> GitStatusEntry {
        GitStatusEntry(
            group: group,
            path: path,
            orig_path: origPath,
            code: code,
            index_status: code,
            worktree_status: ".",
            rename_score: nil,
            submodule: false
        )
    }

    private func status(entries: [GitStatusEntry], counts: GitStatusCounts? = nil, truncated: Bool = false) -> GitStatusResponse {
        GitStatusResponse(
            session_id: "s1",
            cwd: "/repo",
            etag: "e",
            unchanged: false,
            unavailable_reason: nil,
            repo: nil,
            entries: entries,
            counts: counts,
            truncated: truncated,
            entry_limit: 5000
        )
    }

    private func toneName(_ tone: PixelTone) -> String {
        switch tone {
        case .accent: return "accent"
        case .ok: return "ok"
        case .warning: return "warning"
        case .danger: return "danger"
        case .muted: return "muted"
        case .brand: return "brand"
        }
    }

    private func badge(_ entry: GitStatusEntry) -> String {
        let badge = GitChangesDisplay.badge(for: entry)
        return "\(badge.label)|\(toneName(badge.tone))"
    }

    // MARK: - sections(for:)

    func testSectionsFollowTheFixedOrderWithGroupScopedRowIds() throws {
        let sections = GitChangesDisplay.sections(for: try status("linked_worktree_full"))

        XCTAssertEqual(sections.map(\.group), ["conflicts", "staged", "unstaged", "untracked"])
        XCTAssertEqual(sections.map(\.title), ["Conflicts", "Staged", "Changes", "Untracked"])
        XCTAssertEqual(sections.map { $0.rows.count }, [1, 3, 1, 1])
        // `src/both.py` is both staged and unstaged: the group in the key
        // keeps the two rows apart.
        XCTAssertEqual(sections[1].rows.map(\.id), ["staged:1:src/both.py", "staged:1:src/new name.py", "staged:1:vendor/lib"])
        XCTAssertEqual(sections[2].rows.map(\.id), ["unstaged:1:src/both.py"])
    }

    func testEmptyGroupsProduceNoSection() throws {
        XCTAssertEqual(GitChangesDisplay.sections(for: try status("detached_head")), [])
        XCTAssertEqual(GitChangesDisplay.sections(for: try status("upstream_gone")).map(\.group), ["unstaged"])
    }

    func testRepeatedPathsInOneGroupGetDistinctIds() {
        // Two names whose invalid bytes both decode to U+FFFD collide on the
        // bare path; the occurrence counter keeps the second row renderable.
        let sections = GitChangesDisplay.sections(for: status(entries: [
            entry(group: GitStatusEntry.groupUntracked, path: "a\u{FFFD}.txt"),
            entry(group: GitStatusEntry.groupUntracked, path: "b.txt"),
            entry(group: GitStatusEntry.groupUntracked, path: "a\u{FFFD}.txt"),
        ]))

        XCTAssertEqual(
            sections.flatMap(\.rows).map(\.id),
            ["untracked:1:a\u{FFFD}.txt", "untracked:1:b.txt", "untracked:2:a\u{FFFD}.txt"]
        )
    }

    func testUnknownGroupsAppendAfterTheKnownOnesSorted() {
        let sections = GitChangesDisplay.sections(for: status(entries: [
            entry(group: "zeta", path: "z"),
            entry(group: GitStatusEntry.groupUntracked, path: "u"),
            entry(group: "in_progress", path: "i"),
            entry(group: GitStatusEntry.groupConflicts, path: "c", code: "UU"),
        ]))

        XCTAssertEqual(sections.map(\.group), ["conflicts", "untracked", "in_progress", "zeta"])
        XCTAssertEqual(sections.map(\.title), ["Conflicts", "Untracked", "in progress", "zeta"])
    }

    // MARK: - diffScope(for:)

    func testDiffScopePerGroup() {
        XCTAssertEqual(GitChangesDisplay.diffScope(for: entry(group: GitStatusEntry.groupStaged, path: "p")), "staged")
        XCTAssertEqual(GitChangesDisplay.diffScope(for: entry(group: GitStatusEntry.groupUntracked, path: "p")), "untracked")
        XCTAssertEqual(GitChangesDisplay.diffScope(for: entry(group: GitStatusEntry.groupUnstaged, path: "p")), "unstaged")
        XCTAssertEqual(
            GitChangesDisplay.diffScope(for: entry(group: GitStatusEntry.groupConflicts, path: "p", code: "UU")),
            "unstaged",
            "conflicts are diffed against HEAD, which the agent serves as unstaged"
        )
        XCTAssertEqual(GitChangesDisplay.diffScope(for: entry(group: "future", path: "p")), "unstaged")
    }

    // MARK: - badge(for:)

    func testBadgeLetterAndToneFromFixtureEntries() throws {
        let full = try status("linked_worktree_full").entries
        XCTAssertEqual(badge(full[0]), "UU|danger", "conflicts show the raw code")
        XCTAssertEqual(badge(full[1]), "M|warning")
        XCTAssertEqual(badge(full[2]), "R|accent")
        XCTAssertEqual(badge(full[5]), "?|muted", "untracked is always ? whatever the code")

        let gone = try status("upstream_gone").entries
        XCTAssertEqual(badge(gone[0]), "D|danger")
    }

    func testBadgeFallbacksForAddedCopiedAndEmptyCodes() {
        XCTAssertEqual(badge(entry(group: GitStatusEntry.groupStaged, path: "p", code: "A")), "A|ok")
        XCTAssertEqual(badge(entry(group: GitStatusEntry.groupStaged, path: "p", code: "C")), "C|accent")
        XCTAssertEqual(badge(entry(group: GitStatusEntry.groupStaged, path: "p", code: "T")), "T|warning", "an unfamiliar code keeps its letter")
        XCTAssertEqual(badge(entry(group: GitStatusEntry.groupUnstaged, path: "p", code: "")), "M|warning")
        XCTAssertEqual(badge(entry(group: GitStatusEntry.groupConflicts, path: "p", code: "")), "U|danger")
    }

    // MARK: - header labels

    func testHeadLabelForBranchDetachedAndInitial() throws {
        let branch = try XCTUnwrap(try status("linked_worktree_full").repo)
        XCTAssertEqual(GitChangesDisplay.headLabel(branch), "feature/x")

        let detached = try XCTUnwrap(try status("detached_head").repo)
        XCTAssertEqual(GitChangesDisplay.headLabel(detached), "detached @ 0123456")

        let initial = try XCTUnwrap(try status("initial_repo_no_upstream").repo)
        XCTAssertEqual(GitChangesDisplay.headLabel(initial), "No commits yet", "initial wins over the branch name")
    }

    func testUpstreamLabelCountsOrGone() throws {
        let tracked = try XCTUnwrap(try status("linked_worktree_full").repo)
        XCTAssertEqual(GitChangesDisplay.upstreamLabel(tracked.upstream), "↑2 ↓0 · origin/feature/x")

        let gone = try XCTUnwrap(try status("upstream_gone").repo)
        XCTAssertEqual(GitChangesDisplay.upstreamLabel(gone.upstream), "origin/topic · upstream gone")

        XCTAssertNil(GitChangesDisplay.upstreamLabel(nil))
    }

    func testStateLabelHidesCleanAndHumanisesTheRest() {
        XCTAssertNil(GitChangesDisplay.stateLabel(GitRepoInfo.stateClean))
        XCTAssertEqual(GitChangesDisplay.stateLabel(GitRepoInfo.stateCherryPicking), "cherry-picking")
        XCTAssertEqual(GitChangesDisplay.stateLabel(GitRepoInfo.stateMerging), "merging")
        XCTAssertEqual(GitChangesDisplay.stateLabel(GitRepoInfo.stateRebasing), "rebasing")
        XCTAssertEqual(GitChangesDisplay.stateLabel("am_in_progress"), "am in progress")
    }

    // MARK: - row labels

    func testSecondaryLabelPrefersRenameSourceOverDirectory() throws {
        let full = try status("linked_worktree_full").entries
        XCTAssertEqual(GitChangesDisplay.secondaryLabel(full[2]), "src/old.py → src/new name.py")
        XCTAssertEqual(GitChangesDisplay.secondaryLabel(full[1]), "src")

        let initial = try status("initial_repo_no_upstream").entries
        XCTAssertNil(GitChangesDisplay.secondaryLabel(initial[0]), "a top-level file has nothing to show")
    }

    func testTruncatedNoticeShowsWhatArrivedAgainstALowerBoundTotal() throws {
        // The golden case ships 2 entries under a 5000 entry cap with a total
        // of 6001: the shown figure is what arrived, not the cap.
        XCTAssertEqual(
            GitChangesDisplay.truncatedNotice(try status("truncated_windows")),
            "Showing the first 2 of at least 6001 entries."
        )
    }

    func testTruncatedNoticeWithoutAHigherTotalOnlySaysTheListWasCut() {
        let two = [
            entry(group: GitStatusEntry.groupUntracked, path: "a"),
            entry(group: GitStatusEntry.groupUntracked, path: "b"),
        ]
        // The byte cap hit before the entry cap: the count is a lower bound no
        // higher than the list itself, so no total is claimed.
        XCTAssertEqual(
            GitChangesDisplay.truncatedNotice(status(
                entries: two,
                counts: GitStatusCounts(conflicts: 0, staged: 0, unstaged: 0, untracked: 2, total: 2),
                truncated: true
            )),
            "List cut short after 2 entries."
        )
        XCTAssertEqual(
            GitChangesDisplay.truncatedNotice(status(entries: [], counts: nil, truncated: true)),
            "List cut short after 0 entries."
        )
    }

    // MARK: - GitStatusEntry path helpers

    func testFileNameAndDirectorySplitOnTheLastSlash() {
        let nested = entry(group: "staged", path: "a/b/c.txt")
        XCTAssertEqual(nested.fileName, "c.txt")
        XCTAssertEqual(nested.directory, "a/b")

        let top = entry(group: "staged", path: "README.md")
        XCTAssertEqual(top.fileName, "README.md")
        XCTAssertNil(top.directory)

        let rooted = entry(group: "staged", path: "/abs")
        XCTAssertEqual(rooted.fileName, "abs")
        XCTAssertNil(rooted.directory, "an empty head is no directory")

        let spaced = entry(group: "staged", path: "src/new name.py")
        XCTAssertEqual(spaced.fileName, "new name.py")
        XCTAssertEqual(spaced.directory, "src")
    }
}
