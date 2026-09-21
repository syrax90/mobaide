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
            GitItemDisplay.deleteFailureMessage([failure(target: "b", error: "git_timeout")]),
            "Couldn't delete b: git took too long to answer. Try again."
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

    // MARK: - Remote rows, tip line, filter, eligibility

    private func remoteBranch(remote: String? = "origin", gone: Bool = false) -> GitItem {
        GitItem(kind: GitItem.kindRemoteBranch, branch: "origin/feature-a", gone: gone, remote: remote)
    }

    func testRemoteChipNamesTheRemoteAfterCurrentAndBeforeGone() {
        XCTAssertEqual(descriptors(remoteBranch()), ["origin|muted"])
        XCTAssertEqual(descriptors(remoteBranch(remote: nil, gone: true)), ["remote|muted", "gone|danger"])
    }

    func testPrimaryLabelIsTheRemoteQualifiedBranchForARemoteRow() {
        XCTAssertEqual(GitItemDisplay.primaryLabel(remoteBranch()), "origin/feature-a")
        XCTAssertNil(GitItemDisplay.secondaryLabel(remoteBranch()))
    }

    func testShortDateKeepsTheCommitterDayAsAPrefix() {
        XCTAssertEqual(GitItemDisplay.shortDate("2026-09-16T10:20:30+02:00"), "2026-09-16")
        XCTAssertEqual(GitItemDisplay.shortDate("2026-09-16"), "2026-09-16")
        XCTAssertNil(GitItemDisplay.shortDate(nil))
        XCTAssertNil(GitItemDisplay.shortDate(""))
    }

    func testTipLineJoinsSubjectAndDayOrShowsWhicheverThereIs() {
        XCTAssertEqual(
            GitItemDisplay.tipLine(
                GitItem(kind: GitItem.kindBranchOnly, branch: "b", subject: "  Fix login ", committed_at: "2026-09-16T10:20:30+02:00")
            ),
            "Fix login · 2026-09-16"
        )
        XCTAssertEqual(
            GitItemDisplay.tipLine(GitItem(kind: GitItem.kindBranchOnly, branch: "b", subject: "Fix login")),
            "Fix login"
        )
        XCTAssertEqual(
            GitItemDisplay.tipLine(GitItem(kind: GitItem.kindBranchOnly, branch: "b", committed_at: "2026-09-16T10:20:30+02:00")),
            "2026-09-16"
        )
        XCTAssertNil(GitItemDisplay.tipLine(GitItem(kind: GitItem.kindBranchOnly, branch: "b")))
        XCTAssertNil(GitItemDisplay.tipLine(GitItem(kind: GitItem.kindBranchOnly, branch: "b", subject: "   ")))
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

    // MARK: - Branch copy

    private func gitError(_ code: String, _ message: String? = nil) -> SessionGitError {
        .git(code: code, message: message)
    }

    func testBranchMessagesNameTheBranchTheAgentTouched() {
        XCTAssertEqual(
            GitItemDisplay.checkoutConfirmMessage(branch: "feature-a", projectName: "proj"),
            "Check out feature-a in the project folder of proj? Sessions started in that folder afterwards use this branch."
        )
        XCTAssertEqual(
            GitItemDisplay.checkedOutMessage(GitBranchResponse(branch: "feature-a", checked_out: true, created: false)),
            "Checked out feature-a"
        )
        XCTAssertEqual(
            GitItemDisplay.branchCreatedMessage(GitBranchResponse(branch: "feature-a", checked_out: false, created: true)),
            "Created feature-a"
        )
        XCTAssertEqual(
            GitItemDisplay.branchCreatedMessage(GitBranchResponse(branch: "feature-a", checked_out: true, created: true)),
            "Created and checked out feature-a"
        )
    }

    func testCheckoutErrorCopyWordsEachRefusalAndFallsBackToTheSessionCopy() {
        XCTAssertEqual(
            GitItemDisplay.checkoutErrorCopy(gitError("dirty", "error: Your local changes"), branch: "b"),
            "The project folder has local changes this checkout would overwrite. "
                + "Commit or discard them from a session's Changes screen first."
        )
        XCTAssertEqual(
            GitItemDisplay.checkoutErrorCopy(gitError("checked_out_elsewhere", "/wt/topic"), branch: "feature-a"),
            "feature-a is already checked out in another worktree (/wt/topic)."
        )
        XCTAssertEqual(
            GitItemDisplay.checkoutErrorCopy(gitError("checked_out_elsewhere"), branch: "feature-a"),
            "feature-a is already checked out in another worktree."
        )
        XCTAssertEqual(
            GitItemDisplay.checkoutErrorCopy(gitError("operation_in_progress", "merging"), branch: "feature-a"),
            "A merge, rebase or cherry-pick is in progress in the project folder. Finish or abort it first."
        )
        XCTAssertEqual(
            GitItemDisplay.checkoutErrorCopy(gitError("job_running", "job-1"), branch: "feature-a"),
            "A fetch, pull or push is still running in the project folder."
        )
        XCTAssertEqual(
            GitItemDisplay.checkoutErrorCopy(gitError("ref_not_found"), branch: "feature-a"),
            "Branch not found — refresh the list."
        )
        XCTAssertEqual(
            GitItemDisplay.checkoutErrorCopy(gitError("git_error", "fatal: boom"), branch: "feature-a"),
            "git failed: fatal: boom"
        )
    }

    func testCreateBranchErrorCopyNamesTheClashTheBadNameAndTheMissingStartPoint() {
        XCTAssertEqual(
            GitItemDisplay.createBranchErrorCopy(gitError("exists", "feature-a"), name: "feature-a"),
            "A branch named feature-a already exists."
        )
        XCTAssertEqual(
            GitItemDisplay.createBranchErrorCopy(gitError("bad_branch_name"), name: "-x"),
            "Not a valid branch name."
        )
        XCTAssertEqual(
            GitItemDisplay.createBranchErrorCopy(gitError("unknown_start_point", "nope"), name: "feature-a"),
            "Start point nope was not found."
        )
        XCTAssertEqual(
            GitItemDisplay.createBranchErrorCopy(gitError("unknown_start_point"), name: "feature-a"),
            "The start point was not found."
        )
        XCTAssertEqual(
            GitItemDisplay.createBranchErrorCopy(gitError("checked_out_elsewhere", "/wt"), name: "feature-a"),
            "feature-a is already checked out in another worktree (/wt)."
        )
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

    func testRemoteDeleteMessageNamesOneBranchAndCountsSeveral() {
        XCTAssertEqual(
            GitItemDisplay.remoteDeleteMessage(["origin/a"]),
            "Delete origin/a on the remote? This removes it for everyone with access to the remote."
        )
        XCTAssertEqual(
            GitItemDisplay.remoteDeleteMessage(["origin/a", "origin/b"]),
            "Delete 2 branches on the remote? This removes them for everyone with access to the remote."
        )
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
