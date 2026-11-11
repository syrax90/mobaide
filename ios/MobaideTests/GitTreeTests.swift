import XCTest
@testable import Mobaide

/// The project Git screen's folder tree and branch order (`GitTree`,
/// `GitRefOrder`): the same cases the web and Android clients pin, so the
/// three render one list identically.
final class GitTreeTests: XCTestCase {
    private func branch(_ name: String) -> GitItem {
        GitItem(kind: GitItem.kindBranchOnly, branch: name)
    }

    private func remoteBranch(_ name: String, remote: String? = "origin") -> GitItem {
        GitItem(kind: GitItem.kindRemoteBranch, branch: name, remote: remote)
    }

    private func orphan(_ path: String, branch: String? = nil) -> GitItem {
        GitItem(kind: GitItem.kindOrphanWorktree, branch: branch, worktree_path: path)
    }

    /// One line per row, two spaces per depth: `▸ name (count)` / `▾ …` for
    /// folders, the label for rows.
    private func outline(_ rows: [GitTreeRow]) -> [String] {
        rows.map { row in
            let indent = String(repeating: "  ", count: row.depth)
            switch row {
            case .folder(_, let name, _, let count, let expanded):
                return "\(indent)\(expanded ? "▾" : "▸") \(name) (\(count))"
            case .leaf(_, _, let label):
                return "\(indent)\(label)"
            }
        }
    }

    private func folderIDs(_ rows: [GitTreeRow]) -> [String] {
        rows.compactMap { row in
            if case .folder(let id, _, _, _, _) = row { return id }
            return nil
        }
    }

    // MARK: - Order

    func testRefOrderSortsCaseInsensitivelyWithTheRawNameBreakingATie() {
        let names = ["main", "Feature/x", "feature/a", "develop", "fix/b", "alpha", "b", "B"]

        XCTAssertEqual(
            names.sorted(by: GitRefOrder.precedes),
            ["alpha", "B", "b", "develop", "feature/a", "Feature/x", "fix/b", "main"]
        )
        XCTAssertFalse(GitRefOrder.precedes("main", "main"))
    }

    func testRefOrderComparesUTF16CodeUnitsNotScalarsOrLocaleOrder() {
        // U+1F600 is above U+FF5E as a scalar, but its lead surrogate 0xD83D is below it.
        XCTAssertTrue(GitRefOrder.precedes("\u{1F600}", "\u{FF5E}"))
        XCTAssertFalse(GitRefOrder.precedes("\u{FF5E}", "\u{1F600}"))
        XCTAssertEqual(["b", "_x", "1"].sorted(by: GitRefOrder.precedes), ["1", "_x", "b"])
    }

    // MARK: - Segments and ids

    func testSegmentsFileARemoteRowUnderItsWholeRemoteName() {
        XCTAssertEqual(GitTree.segments(remoteBranch("origin/feature/x")), ["origin", "feature", "x"])
        XCTAssertEqual(GitTree.segments(remoteBranch("up/stream/main", remote: "up/stream")), ["up/stream", "main"])
        XCTAssertEqual(GitTree.segments(remoteBranch("origin/main", remote: nil)), ["origin", "main"])
    }

    // MARK: - Rows

    func testRowsPutCollapsedFoldersFirstThenRowsEachLevelAToZIgnoringCase() {
        let items = ["main", "Feature/x", "feature/a", "develop", "fix/b", "alpha"].map(branch)

        XCTAssertEqual(
            outline(GitTree.rows(items, scope: .local, toggled: [], filterActive: false)),
            ["▸ Feature (1)", "▸ feature (1)", "▸ fix (1)", "alpha", "develop", "main"]
        )
    }

    func testRowsOpenOnlyTheToggledFoldersCountingEveryRowBelow() {
        let items = ["feature/login/a", "feature/login/b", "feature/zeta", "feature/Alpha", "main"].map(branch)

        XCTAssertEqual(
            outline(GitTree.rows(items, scope: .local, toggled: ["dir:local:feature"], filterActive: false)),
            ["▾ feature (4)", "  ▸ login (2)", "  Alpha", "  zeta", "main"]
        )

        let rows = GitTree.rows(
            items,
            scope: .local,
            toggled: ["dir:local:feature", "dir:local:feature/login"],
            filterActive: false
        )
        XCTAssertEqual(
            outline(rows),
            ["▾ feature (4)", "  ▾ login (2)", "    a", "    b", "  Alpha", "  zeta", "main"]
        )
        XCTAssertEqual(rows[1].key, "dir:local:feature/login")
        XCTAssertEqual(rows[2], .leaf(item: branch("feature/login/a"), depth: 2, label: "a"))
    }

    func testRowsTrailTheLocalTreeWithOrphanWorktreesByPathWhateverBranchTheyName() {
        let rows = GitTree.rows(
            [orphan("/wt/b"), branch("main"), orphan("/wt/gone", branch: "feature/gone"), orphan("/wt/A"), branch("z/x")],
            scope: .local,
            toggled: [],
            filterActive: false
        )

        XCTAssertEqual(outline(rows), ["▸ z (1)", "main", "/wt/A", "/wt/b", "/wt/gone"])
        XCTAssertEqual(rows.last?.depth, 0)
    }

    func testRowsOpenRemoteNameFoldersByDefaultAndKeepTheirSubFoldersCollapsed() {
        let rows = GitTree.rows(
            [
                remoteBranch("origin/main"),
                remoteBranch("up/stream/main", remote: "up/stream"),
                remoteBranch("origin/feature/x"),
            ],
            scope: .remote,
            toggled: [],
            filterActive: false
        )

        XCTAssertEqual(outline(rows), ["▾ origin (2)", "  ▸ feature (1)", "  main", "▾ up/stream (1)", "  main"])
        XCTAssertEqual(folderIDs(rows), ["dir:remote:origin:", "dir:remote:origin:feature", "dir:remote:up/stream:"])
    }

    func testRowsOpenEveryFolderWhileAFilterIsActiveToggledOrNot() {
        XCTAssertEqual(
            outline(GitTree.rows([branch("feature/login/a"), branch("main")], scope: .local, toggled: [], filterActive: true)),
            ["▾ feature (1)", "  ▾ login (1)", "    a", "main"]
        )
        XCTAssertEqual(
            outline(
                GitTree.rows(
                    [remoteBranch("origin/feature/x")],
                    scope: .remote,
                    toggled: ["dir:remote:origin:"],
                    filterActive: true
                )
            ),
            ["▾ origin (1)", "  ▾ feature (1)", "    x"]
        )
    }
}
