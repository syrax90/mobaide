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

    func testANestedRepositoryListedAsAFolderKeepsItsSlashOnTheName() {
        let worktree = entry(group: "untracked", path: "mobaide_worktree/claude-session-56a3f247/", code: "?")
        XCTAssertEqual(worktree.fileName, "claude-session-56a3f247/")
        XCTAssertEqual(worktree.directory, "mobaide_worktree")

        let topLevel = entry(group: "untracked", path: "vendor/", code: "?")
        XCTAssertEqual(topLevel.fileName, "vendor/")
        XCTAssertNil(topLevel.directory)
    }

    // MARK: - Row and section actions

    private let allCaps = GitCapabilities(stage: true, discard: true, commit: true)

    private var staged: GitStatusEntry { entry(group: GitStatusEntry.groupStaged, path: "src/new name.py", code: "R", origPath: "src/old.py") }
    private var modified: GitStatusEntry { entry(group: GitStatusEntry.groupUnstaged, path: "m.py") }
    private var untracked: GitStatusEntry { entry(group: GitStatusEntry.groupUntracked, path: "notes.txt", code: "?") }
    private var conflict: GitStatusEntry { entry(group: GitStatusEntry.groupConflicts, path: "c.py", code: "UU") }

    private func rows(_ entries: [GitStatusEntry]) -> [GitChangesRow] {
        GitChangesDisplay.sections(for: status(entries: entries)).flatMap(\.rows)
    }

    func testRowActionsUnstageAStagedRowStageOrDiscardTheRestAndLeaveAConflictAlone() {
        XCTAssertEqual(GitChangesDisplay.rowActions(staged, capabilities: allCaps), GitChangesDisplay.RowActions(unstage: true))
        XCTAssertEqual(GitChangesDisplay.rowActions(modified, capabilities: allCaps), GitChangesDisplay.RowActions(stage: true, discard: true))
        XCTAssertEqual(GitChangesDisplay.rowActions(untracked, capabilities: allCaps), GitChangesDisplay.RowActions(stage: true, discard: true))
        XCTAssertEqual(GitChangesDisplay.rowActions(conflict, capabilities: allCaps), .none)
        XCTAssertEqual(GitChangesDisplay.rowActions(entry(group: "future", path: "f"), capabilities: allCaps), .none)
        XCTAssertTrue(GitChangesDisplay.RowActions(discard: true).any)
        XCTAssertFalse(GitChangesDisplay.RowActions.none.any)
    }

    func testRowActionsGateEachActionOnItsOwnCapability() {
        let discardOnly = GitCapabilities(stage: false, discard: true, commit: true)
        let stageOnly = GitCapabilities(stage: true, discard: false, commit: false)

        XCTAssertEqual(GitChangesDisplay.rowActions(modified, capabilities: discardOnly), GitChangesDisplay.RowActions(discard: true))
        XCTAssertEqual(GitChangesDisplay.rowActions(staged, capabilities: discardOnly), .none)
        XCTAssertEqual(GitChangesDisplay.rowActions(modified, capabilities: stageOnly), GitChangesDisplay.RowActions(stage: true))
        XCTAssertEqual(GitChangesDisplay.rowActions(staged, capabilities: stageOnly), GitChangesDisplay.RowActions(unstage: true))
        XCTAssertEqual(GitChangesDisplay.rowActions(modified, capabilities: .none), .none)
    }

    func testDiscardScopeSendsUntrackedToCleanAndTrackedToRestore() {
        XCTAssertEqual(GitChangesDisplay.discardScope(for: untracked), "untracked")
        XCTAssertEqual(GitChangesDisplay.discardScope(for: modified), "unstaged")
        XCTAssertEqual(GitChangesDisplay.discardScope(for: staged), "unstaged")
    }

    func testPathsNameBothSidesOfARenameEachOnce() {
        // `git reset` on the new name alone would leave the deletion staged.
        let alsoOld = entry(group: GitStatusEntry.groupUnstaged, path: "src/old.py")

        XCTAssertEqual(
            GitChangesDisplay.paths(for: rows([staged, alsoOld, untracked])),
            ["src/new name.py", "src/old.py", "notes.txt"]
        )
        XCTAssertEqual(GitChangesDisplay.paths(for: []), [])
    }

    func testSelectionSubsetsSortTheTickedRowsByTheActionEachCanTake() {
        let sections = GitChangesDisplay.sections(for: status(entries: [staged, modified, untracked, conflict]))
        let everything = Set(sections.flatMap(\.rows).map(\.id))

        let subsets = GitChangesDisplay.selectionSubsets(sections, selection: everything, capabilities: allCaps)

        XCTAssertEqual(subsets.stage.map(\.id), ["unstaged:1:m.py", "untracked:1:notes.txt"])
        XCTAssertEqual(subsets.unstage.map(\.id), ["staged:1:src/new name.py"])
        XCTAssertEqual(subsets.discard.map(\.id), ["unstaged:1:m.py", "untracked:1:notes.txt"])
    }

    func testSelectionSubsetsIgnoreUntickedRowsAndActionsTheAgentLacks() {
        let sections = GitChangesDisplay.sections(for: status(entries: [staged, modified, untracked]))
        var caps = allCaps
        caps.discard = false

        let subsets = GitChangesDisplay.selectionSubsets(sections, selection: ["unstaged:1:m.py"], capabilities: caps)

        XCTAssertEqual(subsets.stage.map(\.id), ["unstaged:1:m.py"])
        XCTAssertEqual(subsets.unstage, [])
        XCTAssertEqual(subsets.discard, [])
    }

    func testSectionActionOffersUnstageAllOnStagedStageAllOnChangesAndUntrackedNothingElsewhere() {
        let sections = GitChangesDisplay.sections(for: status(entries: [
            conflict, staged, modified, untracked, entry(group: "ignored", path: "i.txt"),
        ]))

        let byGroup = Dictionary(uniqueKeysWithValues: sections.map { ($0.group, GitChangesDisplay.sectionAction($0, capabilities: allCaps)) })

        XCTAssertEqual(byGroup["conflicts"], .some(nil))
        XCTAssertEqual(byGroup["staged"], GitChangesDisplay.SectionAction(label: "Unstage all", stage: false))
        XCTAssertEqual(byGroup["unstaged"], GitChangesDisplay.SectionAction(label: "Stage all", stage: true))
        XCTAssertEqual(byGroup["untracked"], GitChangesDisplay.SectionAction(label: "Stage all", stage: true))
        XCTAssertEqual(byGroup["ignored"], .some(nil))
        XCTAssertNil(GitChangesDisplay.sectionAction(sections[1], capabilities: GitCapabilities(stage: false, discard: true, commit: true)))
    }

    // MARK: - Discard prompt

    func testDiscardPromptNamesTheOneFileAndSaysWhetherItIsDeletedOrReverted() {
        let untrackedPrompt = GitChangesDisplay.discardPrompt(for: rows([untracked]))
        XCTAssertEqual(untrackedPrompt.title, "Delete untracked file?")
        XCTAssertEqual(untrackedPrompt.body, "notes.txt is not tracked by git and will be deleted. This cannot be undone.")
        XCTAssertEqual(untrackedPrompt.rows.map(\.id), ["untracked:1:notes.txt"])

        let trackedPrompt = GitChangesDisplay.discardPrompt(for: rows([modified]))
        XCTAssertEqual(trackedPrompt.title, "Discard changes?")
        XCTAssertEqual(trackedPrompt.body, "Changes to m.py will be thrown away. This cannot be undone.")
    }

    func testDiscardPromptCountsAMultiFileDiscard() {
        let prompt = GitChangesDisplay.discardPrompt(for: rows([modified, untracked]))

        XCTAssertEqual(prompt.title, "Discard 2 files?")
        XCTAssertEqual(prompt.body, "Their working-tree changes will be thrown away and untracked ones deleted. This cannot be undone.")
    }

    // MARK: - Commit copy

    func testComposeCommitMessageJoinsSubjectAndBodyTheWayGitCommitReadsThem() {
        XCTAssertEqual(GitChangesDisplay.composeCommitMessage(summary: "  Fix it  ", description: ""), "Fix it")
        XCTAssertEqual(GitChangesDisplay.composeCommitMessage(summary: "Fix it", description: "  \n"), "Fix it")
        XCTAssertEqual(GitChangesDisplay.composeCommitMessage(summary: "Fix it", description: "  Why.\n"), "Fix it\n\nWhy.")
        XCTAssertEqual(GitChangesDisplay.composeCommitMessage(summary: "   ", description: "Why."), "\n\nWhy.", "a blank summary is the model's guard, not the composer's")
    }

    func testCommitButtonLabelCountsTheStagedFilesOrReadsAsAnAmend() {
        XCTAssertEqual(GitChangesDisplay.commitButtonLabel(stagedCount: 0, amend: false), "Commit 0 files")
        XCTAssertEqual(GitChangesDisplay.commitButtonLabel(stagedCount: 1, amend: false), "Commit 1 file")
        XCTAssertEqual(GitChangesDisplay.commitButtonLabel(stagedCount: 3, amend: false), "Commit 3 files")
        XCTAssertEqual(GitChangesDisplay.commitButtonLabel(stagedCount: 3, amend: true), "Amend commit")
    }

    func testCommitNoticeAndAuthorLineMatchTheOtherClients() {
        XCTAssertEqual(
            GitChangesDisplay.commitNotice(GitCommitResponse(oid: "abc1234def", short_oid: "abc1234", subject: "Fix it")),
            "Committed abc1234 · Fix it"
        )
        XCTAssertEqual(
            GitChangesDisplay.authorLine(GitCommitAuthor(name: "Ada Lovelace", email: "ada@example.com")),
            "Committing as Ada Lovelace <ada@example.com>"
        )
    }
}
