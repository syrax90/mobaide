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

/// The pure half of the Sync sheet and the job banner: the three rows and
/// their reasons, the job copy shared byte-for-byte with Android and the web,
/// the follow-ups a failure allows, and what `GitJobBanner` derives.
final class GitChangesDisplaySyncTests: XCTestCase {
    private let syncCaps = GitCapabilities(stage: true, discard: true, commit: true, fetch: true, pull: true, push: true)
    private let noSync = GitCapabilities(stage: true, discard: true, commit: true)

    private func status(_ name: String) throws -> GitStatusResponse {
        try FixtureLoader.decodeCase(GitStatusResponse.self, path: "dto/agent/GitStatusResponse.json", name: name)
    }

    private func jobCase(_ name: String) throws -> GitJobResponse {
        try FixtureLoader.decodeCase(GitJobResponse.self, path: "dto/agent/GitJobResponse.json", name: name)
    }

    private func repo(
        upstream: GitUpstream? = nil,
        head: GitHead = GitHead(oid: "abc1234def", short_oid: "abc1234", branch: "main", detached: false, initial: false)
    ) -> GitRepoInfo {
        GitRepoInfo(
            toplevel: "/repo", git_dir: "/repo/.git", common_dir: "/repo/.git", is_linked_worktree: false,
            head: head, upstream: upstream, state: "clean"
        )
    }

    private func tracking(_ ahead: Int?, _ behind: Int?) -> GitRepoInfo {
        repo(upstream: GitUpstream(ref: "origin/main", ahead: ahead, behind: behind))
    }

    private func job(
        op: String = GitJobResponse.opPush,
        status: String = GitJobResponse.statusRunning,
        ok: Bool? = nil,
        errorCode: String? = nil,
        message: String? = nil
    ) -> GitJobResponse {
        GitJobResponse(
            job_id: "j1", session_id: "s1", op: op, status: status,
            phase: nil, percent: nil, line: nil, ok: ok, error_code: errorCode, message: message
        )
    }

    private func done(op: String = GitJobResponse.opPush, message: String? = "main -> main") -> GitJobResponse {
        job(op: op, status: GitJobResponse.statusDone, ok: true, message: message)
    }

    private func failed(_ code: String, op: String = GitJobResponse.opPush, message: String? = nil) -> GitJobResponse {
        job(op: op, status: GitJobResponse.statusDone, ok: false, errorCode: code, message: message)
    }

    private func pull(_ repo: GitRepoInfo?) -> GitChangesDisplay.SyncRow? {
        GitChangesDisplay.syncRows(repo, capabilities: syncCaps).first { $0.op == GitJobResponse.opPull }
    }

    private func push(_ repo: GitRepoInfo?) -> GitChangesDisplay.SyncRow? {
        GitChangesDisplay.syncRows(repo, capabilities: syncCaps).first { $0.op == GitJobResponse.opPush }
    }

    func testSyncRowsExistOnlyForTheCapabilitiesTheAgentAdvertises() {
        XCTAssertEqual(GitChangesDisplay.syncRows(tracking(1, 1), capabilities: noSync), [])
        XCTAssertEqual(
            GitChangesDisplay.syncRows(tracking(1, 1), capabilities: GitCapabilities(fetch: true)).map(\.op),
            ["fetch"]
        )
        XCTAssertEqual(GitChangesDisplay.syncRows(tracking(1, 1), capabilities: syncCaps).map(\.op), ["fetch", "pull", "push"])
    }

    func testSyncRowsAlwaysLetAFetchThrough() {
        XCTAssertEqual(
            GitChangesDisplay.syncRows(repo(), capabilities: syncCaps).first,
            GitChangesDisplay.SyncRow(op: "fetch", label: "Fetch", detail: "Update remote-tracking branches", enabled: true)
        )
    }

    func testSyncRowsEnablePullOnlyWithCommitsBehindAndSayWhyNotOtherwise() {
        XCTAssertEqual(
            pull(tracking(0, 2)),
            GitChangesDisplay.SyncRow(op: "pull", label: "Pull", detail: "Fast-forward 2 commits from origin/main", enabled: true)
        )
        XCTAssertEqual(pull(tracking(0, 1))?.detail, "Fast-forward 1 commit from origin/main")
        XCTAssertEqual(pull(tracking(3, 0)), GitChangesDisplay.SyncRow(op: "pull", label: "Pull", detail: "Nothing to pull", enabled: false))
        XCTAssertEqual(pull(repo()), GitChangesDisplay.SyncRow(op: "pull", label: "Pull", detail: "No upstream", enabled: false))
        XCTAssertEqual(pull(tracking(nil, nil)), GitChangesDisplay.SyncRow(op: "pull", label: "Pull", detail: "Upstream gone", enabled: false))
    }

    func testSyncRowsOfferPublishBranchWithoutAnUpstreamAndPushOnlyWithCommitsAhead() {
        XCTAssertEqual(
            push(repo()),
            GitChangesDisplay.SyncRow(op: "push", label: "Publish branch", detail: "Push and set the upstream", enabled: true)
        )
        XCTAssertEqual(push(tracking(3, 0)), GitChangesDisplay.SyncRow(op: "push", label: "Push", detail: "3 commits to origin/main", enabled: true))
        XCTAssertEqual(push(tracking(1, 2))?.detail, "1 commit to origin/main")
        XCTAssertEqual(push(tracking(0, 2)), GitChangesDisplay.SyncRow(op: "push", label: "Push", detail: "Nothing to push", enabled: false))
        XCTAssertEqual(push(tracking(nil, nil)), GitChangesDisplay.SyncRow(op: "push", label: "Push", detail: "Recreate origin/main", enabled: true))
    }

    func testSyncRowsBlockPullAndPushNeverFetchOnADetachedOrUnbornHeadAndWithoutARepo() throws {
        let detached = try XCTUnwrap(try status("detached_head").repo)
        let initial = try XCTUnwrap(try status("initial_repo_no_upstream").repo)

        XCTAssertEqual(
            GitChangesDisplay.syncRows(detached, capabilities: syncCaps).map { "\($0.op):\($0.enabled):\($0.detail)" },
            ["fetch:true:Update remote-tracking branches", "pull:false:Detached HEAD", "push:false:Detached HEAD"]
        )
        XCTAssertEqual(
            GitChangesDisplay.syncRows(initial, capabilities: syncCaps).dropFirst().map(\.detail),
            ["No commits yet", "No commits yet"]
        )
        XCTAssertEqual(
            GitChangesDisplay.syncRows(nil, capabilities: syncCaps).map { "\($0.enabled):\($0.detail)" },
            ["true:Update remote-tracking branches", "false:Repository unavailable", "false:Repository unavailable"]
        )
    }

    func testSyncRowsFromTheTrackedFixtureOfferAPushOfTwo() throws {
        let tracked = try XCTUnwrap(try status("linked_worktree_full").repo)

        XCTAssertEqual(push(tracked)?.detail, "2 commits to origin/feature/x")
        XCTAssertEqual(pull(tracked)?.detail, "Nothing to pull")
    }

    func testJobTitlesPerOpRunningAndFinished() {
        XCTAssertEqual(["fetch", "pull", "push"].map(GitChangesDisplay.jobTitle), ["Fetching…", "Pulling…", "Pushing…"])
        XCTAssertEqual(GitChangesDisplay.jobDoneTitle("fetch", ok: true), "Fetched")
        XCTAssertEqual(GitChangesDisplay.jobDoneTitle("pull", ok: true), "Pulled")
        XCTAssertEqual(GitChangesDisplay.jobDoneTitle("push", ok: true), "Pushed")
        XCTAssertEqual(GitChangesDisplay.jobDoneTitle("fetch", ok: false), "Fetch failed")
        XCTAssertEqual(GitChangesDisplay.jobDoneTitle("pull", ok: false), "Pull failed")
        XCTAssertEqual(GitChangesDisplay.jobDoneTitle("push", ok: false), "Push failed")
    }

    func testJobErrorCopyPhrasesEveryCodeIdenticallyToTheOtherClients() {
        let copy = { (code: String) in GitChangesDisplay.jobErrorCopy(code, message: nil) }
        XCTAssertEqual(copy("cancelled"), "Cancelled.")
        XCTAssertEqual(copy("git_timeout"), "git took too long to answer. Try again.")
        XCTAssertEqual(copy("auth_required"), "No credentials for this remote. Configure a credential helper or SSH agent on the agent host.")
        XCTAssertEqual(copy("network"), "Couldn't reach the remote.")
        XCTAssertEqual(copy("remote_not_found"), "The remote doesn't exist.")
        XCTAssertEqual(copy("rejected"), "The remote rejected the push.")
        XCTAssertEqual(copy("not_fast_forward"), "Your branch is behind the remote.")
        XCTAssertEqual(copy("conflicts"), "Stopped on conflicts — resolve them in the terminal.")
        XCTAssertEqual(copy("dirty_worktree"), "Commit or stash your changes first.")
        XCTAssertEqual(copy("unmerged"), "Resolve the conflicts first.")
        XCTAssertEqual(copy("operation_in_progress"), "A rebase or merge is already in progress.")
        XCTAssertEqual(copy("no_upstream"), "This branch has no upstream.")
        XCTAssertEqual(copy("no_remote"), "This repository has no remote.")
        XCTAssertEqual(copy("detached_head"), "Detached HEAD — check out a branch first.")
        XCTAssertEqual(copy("no_head"), "No commits yet.")
        XCTAssertEqual(copy("hook_failed"), "A pre-push hook rejected the push.")
        XCTAssertEqual(copy("index_locked"), "Another git process is running — try again.")
    }

    func testJobErrorCopyFallsBackToTheMessageForGitErrorAndUnknownCodes() {
        XCTAssertEqual(GitChangesDisplay.jobErrorCopy("git_error", message: "fatal: boom"), "fatal: boom")
        XCTAssertEqual(GitChangesDisplay.jobErrorCopy("git_error", message: nil), "git failed.")
        XCTAssertEqual(GitChangesDisplay.jobErrorCopy("something_new", message: "details"), "details")
        XCTAssertEqual(GitChangesDisplay.jobErrorCopy("something_new", message: ""), "git failed.")
        XCTAssertEqual(GitChangesDisplay.jobErrorCopy(nil, message: nil), "git failed.")
    }

    func testJobNoticeIsTheTitleTheSummaryOrTheFailureCopy() throws {
        XCTAssertEqual(GitChangesDisplay.jobNotice(job()), "Pushing…")
        XCTAssertEqual(GitChangesDisplay.jobNotice(try jobCase("running_progress")), "Pushing…")
        XCTAssertEqual(GitChangesDisplay.jobNotice(try jobCase("done_ok")), "Pushed · 443f30d..9419146 main -> main")
        XCTAssertEqual(GitChangesDisplay.jobNotice(done(message: nil)), "Pushed")
        XCTAssertEqual(GitChangesDisplay.jobNotice(done(op: "pull", message: "")), "Pulled")
        XCTAssertEqual(GitChangesDisplay.jobNotice(try jobCase("done_rejected")), "Push failed: The remote rejected the push.")
        XCTAssertEqual(GitChangesDisplay.jobNotice(try jobCase("done_cancelled_fetch")), "Fetch failed: Cancelled.")
        XCTAssertEqual(
            GitChangesDisplay.jobNotice(try jobCase("done_pull_conflicts")),
            "Pull failed: Stopped on conflicts — resolve them in the terminal."
        )
        XCTAssertEqual(GitChangesDisplay.jobNotice(failed("git_error", op: "fetch", message: "fatal: boom")), "Fetch failed: fatal: boom")
    }

    func testShowsJobOutputOnlyWhenTheCopyDidNotAlreadySayIt() throws {
        XCTAssertTrue(GitChangesDisplay.showsJobOutput(try jobCase("done_rejected")))
        XCTAssertFalse(GitChangesDisplay.showsJobOutput(failed("git_error", message: "fatal: boom")))
        XCTAssertTrue(GitChangesDisplay.showsJobOutput(try jobCase("done_cancelled_fetch")))
        XCTAssertFalse(GitChangesDisplay.showsJobOutput(failed("no_remote", message: nil)))
        XCTAssertFalse(GitChangesDisplay.showsJobOutput(try jobCase("done_ok")))
        XCTAssertFalse(GitChangesDisplay.showsJobOutput(job(message: "x")))
    }

    func testJobFollowUpsRebaseOrMergeBehindTheRemoteLeaseTooAfterAPushLeaseAloneAfterARejection() throws {
        XCTAssertEqual(GitChangesDisplay.jobFollowUps(failed("not_fast_forward", op: "pull")), [.pullRebase, .pullMerge])
        XCTAssertEqual(GitChangesDisplay.jobFollowUps(failed("not_fast_forward", op: "push")), [.pullRebase, .pullMerge, .forcePush])
        XCTAssertEqual(GitChangesDisplay.jobFollowUps(try jobCase("done_rejected")), [.forcePush])
        XCTAssertEqual(GitChangesDisplay.jobFollowUps(failed("rejected", op: "pull")), [])
        XCTAssertEqual(GitChangesDisplay.jobFollowUps(try jobCase("done_pull_conflicts")), [])
        XCTAssertEqual(GitChangesDisplay.jobFollowUps(try jobCase("done_ok")), [])
        XCTAssertEqual(GitChangesDisplay.jobFollowUps(try jobCase("running_progress")), [])
    }

    func testJobFollowUpsDropThePullsWhenTheStatusSaysNoUpstreamNeverWhenItIsUnavailable() {
        XCTAssertEqual(GitChangesDisplay.jobFollowUps(failed("not_fast_forward", op: "push"), repo: repo()), [.forcePush])
        XCTAssertEqual(GitChangesDisplay.jobFollowUps(failed("not_fast_forward", op: "pull"), repo: repo()), [])
        XCTAssertEqual(GitChangesDisplay.jobFollowUps(failed("rejected", op: "push"), repo: repo()), [.forcePush])
        XCTAssertEqual(GitChangesDisplay.jobFollowUps(failed("not_fast_forward", op: "push"), repo: tracking(nil, nil)), [.forcePush])
        XCTAssertEqual(GitChangesDisplay.jobFollowUps(failed("not_fast_forward", op: "pull"), repo: tracking(nil, nil)), [])
        XCTAssertEqual(
            GitChangesDisplay.jobFollowUps(failed("not_fast_forward", op: "push"), repo: tracking(1, 1)),
            [.pullRebase, .pullMerge, .forcePush]
        )
        XCTAssertEqual(
            GitChangesDisplay.jobFollowUps(failed("not_fast_forward", op: "pull"), repo: tracking(0, 1)),
            [.pullRebase, .pullMerge]
        )
        XCTAssertEqual(
            GitChangesDisplay.jobFollowUps(failed("not_fast_forward", op: "push"), repo: nil),
            [.pullRebase, .pullMerge, .forcePush]
        )
    }

    func testFollowUpsAreLabelledAndConfirmedNamingTheUpstreamAndBranchWhenKnown() {
        XCTAssertEqual(GitChangesDisplay.followUpLabel(.pullRebase), "Pull with rebase…")
        XCTAssertEqual(GitChangesDisplay.followUpLabel(.pullMerge), "Pull with merge…")
        XCTAssertEqual(GitChangesDisplay.followUpLabel(.forcePush), "Force push (with lease)…")
        XCTAssertEqual(
            GitChangesDisplay.followUpPrompt(.pullRebase, repo: tracking(1, 1)),
            SyncPrompt(followUp: .pullRebase, title: "Pull with rebase?", body: "Replays your commits on top of origin/main.", confirm: "Rebase")
        )
        XCTAssertEqual(
            GitChangesDisplay.followUpPrompt(.pullMerge, repo: tracking(1, 1)),
            SyncPrompt(followUp: .pullMerge, title: "Pull with merge?", body: "Merges origin/main into main.", confirm: "Merge")
        )
        XCTAssertEqual(
            GitChangesDisplay.followUpPrompt(.forcePush, repo: tracking(1, 1)),
            SyncPrompt(
                followUp: .forcePush,
                title: "Force push?",
                body: "Overwrites origin/main with your branch. Proceeds only if the remote hasn't moved since your last fetch.",
                confirm: "Force push"
            )
        )
        XCTAssertEqual(GitChangesDisplay.followUpPrompt(.pullMerge, repo: nil).body, "Merges the upstream into your branch.")
    }

    func testGitJobBannerDerivesRunningFailedAndTextAndALostJobHoldsNothing() {
        let running = GitJobBanner(jobId: "j1", job: job())
        XCTAssertTrue(running.running)
        XCTAssertFalse(running.failed)
        XCTAssertEqual(running.text(), "Pushing…")

        let waiting = GitJobBanner(jobId: "j1", job: nil)
        XCTAssertTrue(waiting.running)
        XCTAssertEqual(waiting.text(), "Syncing…")

        let ok = GitJobBanner(jobId: "j1", job: done())
        XCTAssertFalse(ok.running)
        XCTAssertFalse(ok.failed)
        XCTAssertEqual(ok.text(), "Pushed · main -> main")

        let rejected = GitJobBanner(jobId: "j1", job: failed("rejected"))
        XCTAssertFalse(rejected.running)
        XCTAssertTrue(rejected.failed)

        let lost = GitJobBanner(jobId: "j1", job: job(), lost: true)
        XCTAssertFalse(lost.running)
        XCTAssertTrue(lost.failed)
        XCTAssertEqual(lost.text(), "Lost track of the job — refresh to see the result.")
        XCTAssertEqual(GitChangesDisplay.jobLostCopy, "Lost track of the job — refresh to see the result.")
    }

    func testAConflictStopPointsAtTheConflictsSectionOnlyWhenTheAgentResolvesInTheApp() {
        let stopped = failed("conflicts", op: GitJobResponse.opPull)

        XCTAssertEqual(
            GitChangesDisplay.jobErrorCopy("conflicts", message: nil, canResolveInApp: true),
            "Stopped on conflicts — open a file under Conflicts to resolve it."
        )
        XCTAssertEqual(
            GitChangesDisplay.jobErrorCopy("conflicts", message: nil, canResolveInApp: false),
            "Stopped on conflicts — resolve them in the terminal."
        )
        XCTAssertEqual(
            GitChangesDisplay.jobNotice(stopped, canResolveInApp: true),
            "Pull failed: Stopped on conflicts — open a file under Conflicts to resolve it."
        )
        XCTAssertEqual(
            GitJobBanner(jobId: "j1", job: stopped).text(canResolveInApp: true),
            "Pull failed: Stopped on conflicts — open a file under Conflicts to resolve it."
        )
        XCTAssertEqual(
            GitJobBanner(jobId: "j1", job: stopped).text(),
            "Pull failed: Stopped on conflicts — resolve them in the terminal."
        )
        // every other code reads the same either way
        XCTAssertEqual(GitChangesDisplay.jobErrorCopy("unmerged", message: nil, canResolveInApp: true), "Resolve the conflicts first.")
    }
}

/// The pure half of the rebase flow: the sheet's picker and blocker, the
/// banner, the Skip / Abort confirmations, the outcome copy and Mark
/// resolved — the same strings as Android and the web.
final class GitChangesDisplayRebaseTests: XCTestCase {
    private func status(_ name: String) throws -> GitStatusResponse {
        try FixtureLoader.decodeCase(GitStatusResponse.self, path: "dto/agent/GitStatusResponse.json", name: name)
    }

    private let stopped = GitRebaseCommit(oid: "fedcba98", short_oid: "fedcba9", subject: "Fix the login redirect")

    private func info(
        branch: String? = "topic",
        ontoRef: String? = "main",
        ontoShortOid: String? = "89abcde",
        stoppedOn: GitRebaseCommit?? = .none,
        step: Int? = 2,
        total: Int? = 5
    ) -> GitRebaseInfo {
        GitRebaseInfo(
            branch: branch, onto_oid: "89abcdef0123", onto_short_oid: ontoShortOid, onto_ref: ontoRef,
            stopped_on: stoppedOn ?? stopped, step: step, total: total
        )
    }

    private func repo(
        state: String = GitRepoInfo.stateClean,
        head: GitHead = GitHead(oid: "abc1234def", short_oid: "abc1234", branch: "topic", detached: false, initial: false),
        rebase: GitRebaseInfo? = nil
    ) -> GitRepoInfo {
        GitRepoInfo(
            toplevel: "/repo", git_dir: "/repo/.git", common_dir: "/repo/.git", is_linked_worktree: false,
            head: head, upstream: nil, state: state, rebase: rebase
        )
    }

    private func rebasing(_ rebase: GitRebaseInfo?) -> GitRepoInfo {
        repo(
            state: GitRepoInfo.stateRebasing,
            head: GitHead(oid: "89abcdef", short_oid: "89abcde", branch: nil, detached: true, initial: false),
            rebase: rebase
        )
    }

    private func counts(conflicts: Int = 0, staged: Int = 0, unstaged: Int = 0, untracked: Int = 0) -> GitStatusCounts {
        GitStatusCounts(
            conflicts: conflicts, staged: staged, unstaged: unstaged, untracked: untracked,
            total: conflicts + staged + unstaged + untracked
        )
    }

    private func status(repo: GitRepoInfo?, counts: GitStatusCounts?) -> GitStatusResponse {
        GitStatusResponse(
            session_id: "s1", cwd: "/repo", etag: "e", unchanged: false, unavailable_reason: nil,
            repo: repo, entries: [], counts: counts, truncated: false, entry_limit: 5000
        )
    }

    private func target(_ value: String, _ label: String) -> GitChangesDisplay.RebaseTarget {
        GitChangesDisplay.RebaseTarget(value: value, label: label)
    }

    // MARK: - Sheet

    func testRebaseTargetsListLocalBranchesFirstThenRemoteOnesLabelledAsSuchEachSentAsAFullRef() {
        let items = [
            GitItem(kind: GitItem.kindRemoteBranch, branch: "origin/main", remote: "origin"),
            GitItem(kind: GitItem.kindBranchWithWorktree, branch: "topic-b", worktree_path: "/wt/b"),
            GitItem(kind: GitItem.kindBranchOnly, branch: "main"),
            GitItem(kind: GitItem.kindRemoteBranch, branch: "origin/topic", remote: "origin"),
        ]

        XCTAssertEqual(
            GitChangesDisplay.rebaseTargets(items, currentBranch: "topic"),
            [
                target("refs/heads/main", "main"),
                target("refs/heads/topic-b", "topic-b"),
                target("refs/remotes/origin/main", "origin/main (remote)"),
                target("refs/remotes/origin/topic", "origin/topic (remote)"),
            ]
        )
    }

    func testRebaseTargetsLeaveOutTheSessionsOwnBranchByNameNotTheRootsCurrentRow() {
        // A linked-worktree session on `topic`: the project root has `main` checked out.
        let items = [
            GitItem(kind: GitItem.kindBranchOnly, branch: "main", is_current: true),
            GitItem(kind: GitItem.kindBranchWithWorktree, branch: "topic", worktree_path: "/wt/topic"),
        ]

        XCTAssertEqual(GitChangesDisplay.rebaseTargets(items, currentBranch: "topic"), [target("refs/heads/main", "main")])
    }

    func testRebaseTargetsSkipOrphanWorktreesAndBranchlessRowsAndKeepEverythingOnADetachedHead() {
        let items = [
            GitItem(kind: GitItem.kindOrphanWorktree, worktree_path: "/wt/x", detached: true),
            GitItem(kind: GitItem.kindBranchOnly),
            GitItem(kind: GitItem.kindBranchOnly, branch: "main"),
        ]

        XCTAssertEqual(GitChangesDisplay.rebaseTargets(items, currentBranch: nil), [target("refs/heads/main", "main")])
        XCTAssertEqual(GitChangesDisplay.rebaseTargets([], currentBranch: "main"), [])
    }

    func testRebaseTargetsSortTheLocalAndTheRemoteGroupEachAToZIgnoringCase() {
        let items = [
            GitItem(kind: GitItem.kindRemoteBranch, branch: "origin/Zed", remote: "origin"),
            GitItem(kind: GitItem.kindBranchOnly, branch: "zeta"),
            GitItem(kind: GitItem.kindRemoteBranch, branch: "origin/apple", remote: "origin"),
            GitItem(kind: GitItem.kindBranchWithWorktree, branch: "Beta", worktree_path: "/wt/b"),
            GitItem(kind: GitItem.kindBranchOnly, branch: "alpha"),
        ]

        XCTAssertEqual(
            GitChangesDisplay.rebaseTargets(items, currentBranch: nil).map(\.label),
            ["alpha", "Beta", "zeta", "origin/apple (remote)", "origin/Zed (remote)"]
        )
    }

    func testShortRefNameDropsALeadingHeadsOrRemotesPrefixOnce() {
        XCTAssertEqual(GitChangesDisplay.shortRefName("refs/heads/main"), "main")
        XCTAssertEqual(GitChangesDisplay.shortRefName("refs/heads/feature/login"), "feature/login")
        XCTAssertEqual(GitChangesDisplay.shortRefName("refs/remotes/origin/main"), "origin/main")
        XCTAssertEqual(GitChangesDisplay.shortRefName("refs/heads/refs/remotes/x"), "refs/remotes/x")
    }

    func testShortRefNameLeavesATypedRefATagOrANonLeadingPrefixAsItIs() {
        XCTAssertEqual(GitChangesDisplay.shortRefName("main"), "main")
        XCTAssertEqual(GitChangesDisplay.shortRefName("origin/main"), "origin/main")
        XCTAssertEqual(GitChangesDisplay.shortRefName("refs/tags/v1.2.0"), "refs/tags/v1.2.0")
        XCTAssertEqual(GitChangesDisplay.shortRefName("topic/refs/heads/main"), "topic/refs/heads/main")
    }

    func testRebaseBlockerLetsACleanBranchWithCommitsRebaseUntrackedFilesAndAll() {
        XCTAssertNil(GitChangesDisplay.rebaseBlocker(status(repo: repo(), counts: counts())))
        XCTAssertNil(GitChangesDisplay.rebaseBlocker(status(repo: repo(), counts: counts(untracked: 3))))
        XCTAssertNil(GitChangesDisplay.rebaseBlocker(status(repo: repo(), counts: nil)))
    }

    func testRebaseBlockerNamesWhatIsInTheWayTheRepositoryFirst() throws {
        XCTAssertEqual(GitChangesDisplay.rebaseBlocker(nil), "Repository unavailable.")
        XCTAssertEqual(GitChangesDisplay.rebaseBlocker(try status("not_a_repo")), "Repository unavailable.")
        XCTAssertEqual(GitChangesDisplay.rebaseBlocker(try status("linked_worktree_full")), "A rebase or merge is already in progress.")
        XCTAssertEqual(GitChangesDisplay.rebaseBlocker(try status("rebasing_stopped_on_conflict")), "A rebase or merge is already in progress.")
        XCTAssertEqual(GitChangesDisplay.rebaseBlocker(try status("initial_repo_no_upstream")), "This branch has no commits yet.")
        for tracked in [counts(staged: 1), counts(unstaged: 2), counts(conflicts: 1)] {
            XCTAssertEqual(
                GitChangesDisplay.rebaseBlocker(status(repo: repo(), counts: tracked)),
                "Commit or discard your changes first."
            )
        }
    }

    // MARK: - Banner

    func testRebaseBannerIsAbsentUnlessTheRepositoryIsRebasing() throws {
        XCTAssertNil(GitChangesDisplay.rebaseBanner(repo(), conflicts: 0))
        XCTAssertNil(GitChangesDisplay.rebaseBanner(try status("linked_worktree_full").repo, conflicts: 1))
        XCTAssertNil(GitChangesDisplay.rebaseBanner(nil, conflicts: 0))
    }

    func testRebaseBannerFromTheStoppedFixtureNamesTheBranchBaseStopAndStep() throws {
        let stoppedStatus = try status("rebasing_stopped_on_conflict")

        XCTAssertEqual(
            GitChangesDisplay.rebaseBanner(stoppedStatus.repo, conflicts: stoppedStatus.counts?.conflicts ?? 0),
            GitChangesDisplay.RebaseBanner(
                title: "Rebasing topic onto origin/main",
                progress: "Stopped at fedcba9 Fix the login redirect · 2/5",
                hint: "1 conflict — fix the files, then Mark resolved.",
                actionable: true
            )
        )
        XCTAssertEqual(GitChangesDisplay.rebaseBanner(rebasing(info()), conflicts: 3)?.hint, "3 conflicts — fix the files, then Mark resolved.")
        XCTAssertEqual(GitChangesDisplay.rebaseBanner(rebasing(info()), conflicts: 0)?.hint, "No conflicts left — Continue when ready.")
    }

    func testRebaseBannerPointsAtTheFilesThemselvesWhenTheAgentResolvesInTheApp() {
        XCTAssertEqual(
            GitChangesDisplay.rebaseBanner(rebasing(info()), conflicts: 1, canResolveInApp: true)?.hint,
            "1 conflict — open a file to resolve it."
        )
        XCTAssertEqual(
            GitChangesDisplay.rebaseBanner(rebasing(info()), conflicts: 3, canResolveInApp: true)?.hint,
            "3 conflicts — open a file to resolve it."
        )
        XCTAssertEqual(
            GitChangesDisplay.rebaseBanner(rebasing(info()), conflicts: 0, canResolveInApp: true)?.hint,
            "No conflicts left — Continue when ready."
        )
        XCTAssertEqual(
            GitChangesDisplay.rebaseBanner(rebasing(nil), conflicts: 2, canResolveInApp: true)?.hint,
            "Continue, skip or abort it in the terminal."
        )
    }

    func testRebaseBannerFallsBackToTheShortOidThenDropsWhatTheAgentCouldNotRead() {
        XCTAssertEqual(GitChangesDisplay.rebaseBanner(rebasing(info(ontoRef: nil)), conflicts: 0)?.title, "Rebasing topic onto 89abcde")
        XCTAssertEqual(GitChangesDisplay.rebaseBanner(rebasing(info(ontoRef: nil, ontoShortOid: nil)), conflicts: 0)?.title, "Rebasing topic")
        XCTAssertEqual(GitChangesDisplay.rebaseBanner(rebasing(info(branch: nil)), conflicts: 0)?.title, "Rebasing onto main")
        XCTAssertEqual(GitChangesDisplay.rebaseBanner(rebasing(info(stoppedOn: .some(nil))), conflicts: 0)?.progress, "2/5")
        XCTAssertEqual(
            GitChangesDisplay.rebaseBanner(rebasing(info(step: nil)), conflicts: 0)?.progress,
            "Stopped at fedcba9 Fix the login redirect"
        )
    }

    func testRebaseBannerWithNoDetailsAsAGitAmSessionReportsItPointsToTheTerminalWhateverTheConflicts() throws {
        let toTerminal = GitChangesDisplay.RebaseBanner(
            title: "Rebase in progress", progress: nil, hint: "Continue, skip or abort it in the terminal.", actionable: false
        )

        // `upstream_gone` is an older agent's rebasing body: no `rebase` key at all.
        XCTAssertEqual(GitChangesDisplay.rebaseBanner(try status("upstream_gone").repo, conflicts: 0), toTerminal)
        XCTAssertEqual(GitChangesDisplay.rebaseBanner(rebasing(nil), conflicts: 2), toTerminal)
    }

    func testRebaseBannerKeepsTheControlsForARebaseWhoseDetailsTheAgentCouldNotRead() {
        XCTAssertEqual(
            GitChangesDisplay.rebaseBanner(rebasing(GitRebaseInfo()), conflicts: 2),
            GitChangesDisplay.RebaseBanner(
                title: "Rebase in progress", progress: nil, hint: "2 conflicts — fix the files, then Mark resolved.", actionable: true
            )
        )
    }

    // MARK: - Confirmations

    func testRebaseStepPromptSkipNamesTheCommitAbortWarnsTheResolutionsGo() {
        XCTAssertEqual(
            GitChangesDisplay.rebaseStepPrompt(.skip, repo: rebasing(info())),
            RebasePrompt(step: .skip, title: "Skip this commit?", body: "fedcba9 “Fix the login redirect” will be left out of topic.", confirm: "Skip commit")
        )
        XCTAssertEqual(
            GitChangesDisplay.rebaseStepPrompt(.skip, repo: rebasing(info(stoppedOn: .some(nil)))).body,
            "The commit the rebase stopped at will be left out of topic."
        )
        XCTAssertEqual(
            GitChangesDisplay.rebaseStepPrompt(.abort, repo: rebasing(info())),
            RebasePrompt(
                step: .abort,
                title: "Abort the rebase?",
                body: "topic goes back to where it was before the rebase. Conflict resolutions made so far are discarded.",
                confirm: "Abort rebase"
            )
        )
    }

    func testRebasedBranchIsTheRebasedOneThenHeadsThenAGenericName() throws {
        XCTAssertEqual(GitChangesDisplay.rebasedBranch(rebasing(info())), "topic")
        XCTAssertEqual(GitChangesDisplay.rebasedBranch(try status("upstream_gone").repo), "topic")
        XCTAssertEqual(GitChangesDisplay.rebasedBranch(rebasing(nil)), "the branch")
        XCTAssertEqual(GitChangesDisplay.rebasedBranch(nil), "the branch")
    }

    // MARK: - Outcome copy

    func testRebaseOutcomeNoticeReportsAFinishedStartWithTheBaseAFinishedStepWithoutIt() {
        let rebased = GitRebaseResponse(outcome: GitRebaseResponse.outcomeRebased, rebase: nil, message: nil)

        XCTAssertEqual(GitChangesDisplay.rebaseOutcomeNotice(rebased, branch: "topic", onto: "main"), "Rebased topic onto main.")
        XCTAssertEqual(GitChangesDisplay.rebaseOutcomeNotice(rebased, branch: "topic", onto: nil), "Rebase finished.")
        XCTAssertEqual(
            GitChangesDisplay.rebaseOutcomeNotice(
                GitRebaseResponse(outcome: GitRebaseResponse.outcomeUpToDate, rebase: nil, message: nil), branch: "topic", onto: "main"
            ),
            "topic is already up to date with main."
        )
        XCTAssertEqual(
            GitChangesDisplay.rebaseOutcomeNotice(
                GitRebaseResponse(outcome: GitRebaseResponse.outcomeAborted, rebase: nil, message: nil), branch: "topic", onto: nil
            ),
            "Rebase aborted — topic is back where it started."
        )
        XCTAssertEqual(
            GitChangesDisplay.rebaseOutcomeNotice(GitRebaseResponse(outcome: "future_outcome", rebase: nil, message: nil), branch: "topic", onto: nil),
            "Rebase: future_outcome"
        )
    }

    func testRebaseOutcomeNoticeNamesABasePickedAsAFullRefByItsShortName() {
        XCTAssertEqual(
            GitChangesDisplay.rebaseOutcomeNotice(
                GitRebaseResponse(outcome: GitRebaseResponse.outcomeRebased, rebase: nil, message: nil), branch: "topic", onto: "refs/heads/main"
            ),
            "Rebased topic onto main."
        )
        XCTAssertEqual(
            GitChangesDisplay.rebaseOutcomeNotice(
                GitRebaseResponse(outcome: GitRebaseResponse.outcomeUpToDate, rebase: nil, message: nil),
                branch: "topic",
                onto: "refs/remotes/origin/main"
            ),
            "topic is already up to date with origin/main."
        )
    }

    func testRebaseOutcomeToastReportsAStopAsDangerOnItsSubjectOrGitsExplanation() throws {
        let conflict = try FixtureLoader.decodeCase(GitRebaseResponse.self, path: "dto/agent/GitRebaseResponse.json", name: "stopped_on_conflict")
        let untracked = try FixtureLoader.decodeCase(
            GitRebaseResponse.self, path: "dto/agent/GitRebaseResponse.json", name: "stopped_detached_without_details"
        )
        let bare = GitRebaseResponse(outcome: GitRebaseResponse.outcomeStopped, rebase: nil, message: "")

        let conflictToast = GitChangesDisplay.rebaseOutcomeToast(conflict, branch: "topic", onto: "main")
        XCTAssertEqual(conflictToast.text, "Rebase stopped at “Fix the login redirect” — resolve the conflicts, then Continue.")
        XCTAssertEqual(conflictToast.tone, .danger)
        XCTAssertEqual(
            GitChangesDisplay.rebaseOutcomeToast(untracked, branch: "topic", onto: "main").text,
            "Rebase stopped: error: The following untracked working tree files would be overwritten by merge:"
        )
        XCTAssertEqual(
            GitChangesDisplay.rebaseOutcomeToast(bare, branch: "topic", onto: nil).text,
            "Rebase stopped — resolve the conflicts, then Continue."
        )
        XCTAssertEqual(
            GitChangesDisplay.rebaseOutcomeToast(
                GitRebaseResponse(outcome: GitRebaseResponse.outcomeRebased, rebase: nil, message: nil), branch: "topic", onto: nil
            ).tone,
            .ok
        )
    }

    // MARK: - Mark resolved

    private func conflict(_ path: String) -> GitStatusEntry {
        GitStatusEntry(
            group: GitStatusEntry.groupConflicts, path: path, orig_path: nil, code: "UU",
            index_status: "U", worktree_status: "U", rename_score: nil, submodule: false
        )
    }

    func testRowActionsMarkAConflictResolvedOnlyOnAnAgentThatStagesAndGuardsTheMarkers() {
        let resolving = GitCapabilities(stage: true, discard: true, commit: true, resolve: true)
        let modified = GitStatusEntry(
            group: GitStatusEntry.groupUnstaged, path: "m.py", orig_path: nil, code: "M",
            index_status: ".", worktree_status: "M", rename_score: nil, submodule: false
        )

        XCTAssertEqual(GitChangesDisplay.rowActions(conflict("c.py"), capabilities: resolving), GitChangesDisplay.RowActions(resolve: true))
        XCTAssertEqual(GitChangesDisplay.rowActions(conflict("c.py"), capabilities: GitCapabilities(stage: false, resolve: true)), .none)
        XCTAssertEqual(GitChangesDisplay.rowActions(conflict("c.py"), capabilities: GitCapabilities(stage: true)), .none)
        XCTAssertFalse(GitChangesDisplay.rowActions(modified, capabilities: resolving).resolve)
        XCTAssertFalse(GitChangesDisplay.RowActions(resolve: true).any, "Mark resolved is per row, never a selection action")
    }

    func testOpensResolverOnlyForAConflictRowOfAnAgentThatServesTheResolver() {
        let withResolver = GitCapabilities(stage: true, discard: true, commit: true, rebase: true, resolve: true, conflicts: true)
        var withoutResolver = withResolver
        withoutResolver.conflicts = false
        let modified = GitStatusEntry(
            group: GitStatusEntry.groupUnstaged, path: "m.py", orig_path: nil, code: "M",
            index_status: ".", worktree_status: "M", rename_score: nil, submodule: false
        )

        XCTAssertTrue(GitChangesDisplay.opensResolver(conflict("c.py"), capabilities: withResolver))
        XCTAssertFalse(GitChangesDisplay.opensResolver(conflict("c.py"), capabilities: withoutResolver))
        XCTAssertFalse(GitChangesDisplay.opensResolver(modified, capabilities: withResolver))
    }

    func testConflictsStayOutOfTheSelectionSubsetsEvenWhenTheyCanBeMarkedResolved() {
        let resolving = GitCapabilities(stage: true, discard: true, commit: true, resolve: true)
        let sections = GitChangesDisplay.sections(for: GitStatusResponse(
            session_id: "s1", cwd: "/repo", etag: "e", unchanged: false, unavailable_reason: nil, repo: nil,
            entries: [conflict("a.py"), conflict("b.py")], counts: nil, truncated: false, entry_limit: 5000
        ))
        let everything = Set(sections.flatMap(\.rows).map(\.id))

        let subsets = GitChangesDisplay.selectionSubsets(sections, selection: everything, capabilities: resolving)

        XCTAssertEqual(subsets.stage, [])
        XCTAssertEqual(subsets.unstage, [])
        XCTAssertEqual(subsets.discard, [])
    }
}
