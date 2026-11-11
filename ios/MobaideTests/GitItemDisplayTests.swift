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

    // MARK: - chips

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

    func testDeleteFailureMessagePrefersErrorDetailFirstLine() {
        XCTAssertEqual(
            GitItemDisplay.deleteFailureMessage([
                failure(target: "b", error: "error", detail: "  fatal: branch 'b' is checked out at '/wt'  \nhint: use --force"),
            ]),
            "Couldn't delete b: fatal: branch 'b' is checked out at '/wt'"
        )
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

    // MARK: - Remote rows, tip line, filter, eligibility

    private func remoteBranch(remote: String? = "origin", gone: Bool = false) -> GitItem {
        GitItem(kind: GitItem.kindRemoteBranch, branch: "origin/feature-a", gone: gone, remote: remote)
    }

    func testPrimaryLabelIsTheRemoteQualifiedBranchForARemoteRow() {
        XCTAssertEqual(GitItemDisplay.primaryLabel(remoteBranch()), "origin/feature-a")
        XCTAssertNil(GitItemDisplay.secondaryLabel(remoteBranch()))
    }

    func testMatchesFilterIsCaseInsensitiveOnTheLabelAndTheWorktreePath() {
        XCTAssertTrue(GitItemDisplay.matchesFilter(GitItem(kind: GitItem.kindBranchOnly, branch: "Feature/Login"), " login "))
        XCTAssertTrue(GitItemDisplay.matchesFilter(remoteBranch(), "ORIGIN/"))
        XCTAssertTrue(
            GitItemDisplay.matchesFilter(
                GitItem(kind: GitItem.kindBranchWithWorktree, branch: "b", worktree_path: "/wt/Alpha"), "alpha"
            )
        )
        XCTAssertTrue(GitItemDisplay.matchesFilter(GitItem(kind: GitItem.kindOrphanWorktree, worktree_path: "/wt/x"), "wt/x"))
        XCTAssertTrue(GitItemDisplay.matchesFilter(GitItem(kind: GitItem.kindBranchOnly, branch: "b"), "  "))
        XCTAssertFalse(GitItemDisplay.matchesFilter(GitItem(kind: GitItem.kindBranchOnly, branch: "b"), "main"))
    }

    func testCheckOutIsOfferedWhereNothingHasTheBranchCheckedOutAndRemoteRowsNeverDelete() {
        XCTAssertTrue(GitItem(kind: GitItem.kindBranchOnly, branch: "b").canCheckOut)
        XCTAssertTrue(remoteBranch().canCheckOut)
        XCTAssertFalse(GitItem(kind: GitItem.kindBranchOnly, branch: "b", is_current: true).canCheckOut)
        XCTAssertFalse(GitItem(kind: GitItem.kindBranchWithWorktree, branch: "b", worktree_path: "/wt").canCheckOut)
        XCTAssertFalse(GitItem(kind: GitItem.kindOrphanWorktree, worktree_path: "/wt").canCheckOut)
        XCTAssertFalse(remoteBranch().isDeletable(deleteRemote: false))
        XCTAssertEqual(remoteBranch().id, "remote_branch:origin/feature-a|")
    }

    // MARK: - Remote delete and Remove worktree

    func testRemoteRowsDeleteOnlyBehindTheCapabilityAndOnlyLinkedWorktreesCanBeRemoved() {
        XCTAssertTrue(remoteBranch().isDeletable(deleteRemote: true))
        XCTAssertFalse(remoteBranch().isDeletable(deleteRemote: false))
        XCTAssertFalse(GitItem(kind: GitItem.kindBranchOnly, branch: "main", is_current: true).isDeletable(deleteRemote: true))
        XCTAssertTrue(GitItem(kind: GitItem.kindBranchOnly, branch: "b").isDeletable(deleteRemote: false))

        XCTAssertTrue(GitItem(kind: GitItem.kindBranchWithWorktree, branch: "b", worktree_path: "/wt/b").canRemoveWorktree)
        XCTAssertFalse(
            GitItem(kind: GitItem.kindBranchWithWorktree, branch: "main", worktree_path: "/repo", is_current: true)
                .canRemoveWorktree
        )
        XCTAssertFalse(GitItem(kind: GitItem.kindBranchWithWorktree, branch: "b", worktree_path: "").canRemoveWorktree)
        XCTAssertFalse(GitItem(kind: GitItem.kindBranchOnly, branch: "b").canRemoveWorktree)
        XCTAssertFalse(GitItem(kind: GitItem.kindOrphanWorktree, worktree_path: "/wt/x").canRemoveWorktree)
        XCTAssertFalse(remoteBranch().canRemoveWorktree)
    }

    func testDeleteFailureReasonGivesAuthRequiredItsOwnCopyAndKeepsTheRemoteReasonOtherwise() {
        XCTAssertEqual(
            GitItemDisplay.deleteFailureReason(
                GitDeleteResultItem(
                    kind: GitItem.kindRemoteBranch,
                    target: "origin/feature-a",
                    ok: false,
                    error: "auth_required",
                    error_detail: "fatal: could not read Username for 'https://example.com': terminal prompts disabled"
                )
            ),
            "No credentials for this remote. Configure a credential helper or SSH agent on the agent host."
        )
        XCTAssertEqual(
            GitItemDisplay.deleteFailureReason(
                GitDeleteResultItem(
                    kind: GitItem.kindRemoteBranch,
                    target: "origin/main",
                    ok: false,
                    error: "rejected",
                    error_detail: "[remote rejected] (deletion of the current branch prohibited)"
                )
            ),
            "[remote rejected] (deletion of the current branch prohibited)"
        )
    }
}
