import XCTest
@testable import Mobaide

/// Pure label/chip derivations for the git list rows (`GitItemDisplay`), across
/// the three `GitItem` kinds. The row view itself is not tested (SwiftUI).
final class GitItemDisplayTests: XCTestCase {
    private func styleName(_ style: GitItemDisplay.ChipStyle) -> String {
        switch style {
        case .highlight: return "highlight"
        case .danger: return "danger"
        case .neutral: return "neutral"
        case .muted: return "muted"
        }
    }

    /// Flattens `(text, style)` chips into order-, text-, and style-sensitive
    /// descriptors so a single `XCTAssertEqual` covers all three.
    private func descriptors(_ item: GitItem) -> [String] {
        GitItemDisplay.chips(item).map { "\($0.text)|\(self.styleName($0.style))" }
    }

    // MARK: - primaryLabel

    func testPrimaryLabelIsBranchForBranchKinds() {
        XCTAssertEqual(
            GitItemDisplay.primaryLabel(GitItem(kind: GitItem.kindBranchOnly, branch: "main")),
            "main"
        )
        XCTAssertEqual(
            GitItemDisplay.primaryLabel(
                GitItem(kind: GitItem.kindBranchWithWorktree, branch: "feature", worktree_path: "/wt")
            ),
            "feature"
        )
    }

    func testPrimaryLabelIsWorktreePathForOrphan() {
        XCTAssertEqual(
            GitItemDisplay.primaryLabel(GitItem(kind: GitItem.kindOrphanWorktree, worktree_path: "/wt/x")),
            "/wt/x"
        )
    }

    func testPrimaryLabelFallsBackToEmptyWhenMissing() {
        XCTAssertEqual(GitItemDisplay.primaryLabel(GitItem(kind: GitItem.kindBranchOnly)), "")
        XCTAssertEqual(GitItemDisplay.primaryLabel(GitItem(kind: GitItem.kindOrphanWorktree)), "")
    }

    // MARK: - secondaryLabel

    func testSecondaryLabelIsWorktreePathForBranchWithWorktree() {
        XCTAssertEqual(
            GitItemDisplay.secondaryLabel(
                GitItem(kind: GitItem.kindBranchWithWorktree, branch: "feature", worktree_path: "/wt")
            ),
            "/wt"
        )
    }

    func testSecondaryLabelIsNilForBranchOnly() {
        XCTAssertNil(GitItemDisplay.secondaryLabel(GitItem(kind: GitItem.kindBranchOnly, branch: "main")))
    }

    func testSecondaryLabelReflectsDetachedForOrphan() {
        XCTAssertEqual(
            GitItemDisplay.secondaryLabel(
                GitItem(kind: GitItem.kindOrphanWorktree, worktree_path: "/wt", detached: true)
            ),
            "(detached HEAD)"
        )
        XCTAssertNil(
            GitItemDisplay.secondaryLabel(
                GitItem(kind: GitItem.kindOrphanWorktree, worktree_path: "/wt", detached: false)
            )
        )
    }

    // MARK: - chips

    func testNoChipsForPlainBranch() {
        XCTAssertEqual(descriptors(GitItem(kind: GitItem.kindBranchOnly, branch: "main")), [])
    }

    func testCurrentGoneAndPrunableChipStyles() {
        XCTAssertEqual(
            descriptors(GitItem(kind: GitItem.kindBranchOnly, branch: "main", is_current: true)),
            ["current|highlight"]
        )
        XCTAssertEqual(
            descriptors(GitItem(kind: GitItem.kindBranchOnly, branch: "old", gone: true)),
            ["gone|danger"]
        )
        XCTAssertEqual(
            descriptors(GitItem(kind: GitItem.kindBranchOnly, branch: "old", prunable: true)),
            ["prunable|muted"]
        )
    }

    func testAheadBehindChipAppearsOnlyWhenNonZero() {
        XCTAssertEqual(
            descriptors(GitItem(kind: GitItem.kindBranchOnly, branch: "b", ahead: 2, behind: 0)),
            ["ahead 2 / behind 0|neutral"]
        )
        XCTAssertEqual(
            descriptors(GitItem(kind: GitItem.kindBranchOnly, branch: "b", ahead: 0, behind: 5)),
            ["ahead 0 / behind 5|neutral"]
        )
        XCTAssertEqual(
            descriptors(GitItem(kind: GitItem.kindBranchOnly, branch: "b", ahead: 0, behind: 0)),
            []
        )
    }

    func testLockedChipFormatsReason() {
        XCTAssertEqual(
            descriptors(GitItem(kind: GitItem.kindBranchOnly, branch: "b", locked: true, locked_reason: "in use")),
            ["locked: in use|danger"]
        )
        XCTAssertEqual(
            descriptors(GitItem(kind: GitItem.kindBranchOnly, branch: "b", locked: true, locked_reason: nil)),
            ["locked|danger"]
        )
        XCTAssertEqual(
            descriptors(GitItem(kind: GitItem.kindBranchOnly, branch: "b", locked: true, locked_reason: "   ")),
            ["locked|danger"]
        )
    }

    func testOrphanChipPresentForOrphanKind() {
        XCTAssertEqual(
            descriptors(GitItem(kind: GitItem.kindOrphanWorktree, worktree_path: "/wt", prunable: true)),
            ["orphan|danger", "prunable|muted"]
        )
    }

    // MARK: - deleteFailureMessage

    private func failure(target: String, error: String?, detail: String? = nil) -> GitDeleteResultItem {
        GitDeleteResultItem(
            kind: GitItem.kindBranchOnly,
            target: target,
            ok: false,
            error: error,
            error_detail: detail
        )
    }

    func testDeleteFailureMessageNamesTargetAndReason() {
        XCTAssertEqual(
            GitItemDisplay.deleteFailureMessage([failure(target: "wt", error: "locked")]),
            "Couldn't delete wt: the worktree is locked"
        )
        XCTAssertEqual(
            GitItemDisplay.deleteFailureMessage([failure(target: "b", error: "not_found")]),
            "Couldn't delete b: it no longer exists"
        )
        XCTAssertEqual(
            GitItemDisplay.deleteFailureMessage([failure(target: "b", error: "invalid")]),
            "Couldn't delete b: the request was invalid"
        )
        XCTAssertEqual(
            GitItemDisplay.deleteFailureMessage([failure(target: "b", error: "error")]),
            "Couldn't delete b: unknown error"
        )
    }

    func testDeleteFailureMessagePrefersErrorDetailFirstLine() {
        XCTAssertEqual(
            GitItemDisplay.deleteFailureMessage([
                failure(target: "b", error: "error", detail: "  fatal: branch 'b' is checked out at '/wt'  \nhint: use --force"),
            ]),
            "Couldn't delete b: fatal: branch 'b' is checked out at '/wt'"
        )
    }

    func testDeleteFailureMessageTruncatesLongDetail() {
        let detail = String(repeating: "x", count: 200)
        let message = GitItemDisplay.deleteFailureMessage([failure(target: "b", error: "error", detail: detail)])
        XCTAssertEqual(message, "Couldn't delete b: " + String(repeating: "x", count: 120))
    }

    func testDeleteFailureMessageFallsBackToACountForSeveralFailures() {
        XCTAssertEqual(
            GitItemDisplay.deleteFailureMessage([
                failure(target: "b1", error: "locked"),
                failure(target: "b2", error: "error"),
            ]),
            "2 item(s) failed to delete"
        )
    }

    func testChipOrderIsStableAcrossAllStatuses() {
        let item = GitItem(
            kind: GitItem.kindBranchWithWorktree,
            branch: "feature",
            worktree_path: "/wt",
            is_current: true,
            ahead: 2,
            behind: 3,
            gone: true,
            locked: true,
            locked_reason: "held",
            prunable: true
        )
        XCTAssertEqual(descriptors(item), [
            "current|highlight",
            "gone|danger",
            "ahead 2 / behind 3|neutral",
            "locked: held|danger",
            "prunable|muted",
        ])
    }
}
