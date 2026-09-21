import XCTest
@testable import Mobaide

/// The rebase half of `GitChangesModel` over the real `AgentAPI` (stubbed
/// transport): the sheet's branch list, what a start and each step send, the
/// toast or in-sheet error each answer leaves, the confirmation's
/// prompt/pending split, and Mark resolved through the stage route.
@MainActor
final class GitRebaseModelTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeModel(
        projectName: String? = "proj",
        openRebase: Bool = false
    ) -> GitChangesModel {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let factory = FakeWebSocketTransportFactory()
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: client
        )
        return GitChangesModel(
            sessionId: "s1",
            agentAPI: agentAPI,
            agentEventBus: bus,
            projectName: { projectName },
            sleeper: ManualSleeper(),
            openRebase: openRebase
        )
    }

    private let allGit = #"{"changes":true,"stage":true,"discard":true,"commit":true,"rebase":true,"resolve":true}"#

    /// `fixture` on screen with every git capability, the probe's and the
    /// load's traffic already forgotten.
    private func loadedModel(
        _ fixture: String,
        projectName: String? = nil,
        git: String? = nil
    ) async throws -> GitChangesModel {
        let model = makeModel(projectName: projectName)
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"name":"mobaide-agent","server_version":"1.4.0","protocol_versions":[7],"min_client_version":1,"capabilities":{"git":\#(git ?? allGit)}}"#
        )
        await model.loadCapabilities()
        StubURLProtocol.enqueue(status: 200, body: try statusBody(fixture))
        await model.load()
        StubURLProtocol.reset()
        return model
    }

    /// The sheet open on a session without a project, its target list settled.
    private func openTypedSheet(_ model: GitChangesModel) async {
        model.openRebase()
        await waitUntil { model.rebaseSheet?.targets == .typed }
    }

    private func statusBody(_ name: String) throws -> String {
        String(decoding: try FixtureLoader.wireData(path: "dto/agent/GitStatusResponse.json", case: name), as: UTF8.self)
    }

    private func rebaseBody(_ name: String) throws -> String {
        String(decoding: try FixtureLoader.wireData(path: "dto/agent/GitRebaseResponse.json", case: name), as: UTF8.self)
    }

    private var requestURLs: [String] {
        StubURLProtocol.requests.compactMap { $0.url?.absoluteString }
    }

    private func gitURL(_ leaf: String) -> String { "http://agent.example.com/api/sessions/s1/git/\(leaf)" }

    private func sentBody<T: Decodable>(_ type: T.Type, at index: Int) throws -> T {
        let bodies = StubURLProtocol.bodies
        XCTAssertTrue(bodies.indices.contains(index), "no request body at \(index)")
        return try JSONDecoder().decode(type, from: bodies[index])
    }

    // MARK: - Sheet

    func testOpenRebaseListsTheProjectsBranchesWithRemotesAndCloseDropsIt() async throws {
        let model = makeModel(projectName: "proj")
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"items":[{"kind":"branch_only","branch":"main"},{"kind":"remote_branch","branch":"origin/main","remote":"origin"}]}"#
        )

        model.openRebase()
        XCTAssertEqual(model.rebaseSheet, RebaseSheetState(targets: .loading))
        await waitUntil { model.rebaseSheet?.targets != .loading }

        XCTAssertEqual(requestURLs, ["http://agent.example.com/api/projects/proj/git/items?remote=true"])
        XCTAssertEqual(
            model.rebaseSheet?.targets,
            .ready([
                GitItem(kind: GitItem.kindBranchOnly, branch: "main"),
                GitItem(kind: GitItem.kindRemoteBranch, branch: "origin/main", remote: "origin"),
            ])
        )

        model.closeRebase()
        XCTAssertNil(model.rebaseSheet)
    }

    func testOpenRebaseTypesTheRefWithoutAProjectOrWhenTheListFails() async throws {
        let noProject = makeModel(projectName: nil)
        await openTypedSheet(noProject)
        XCTAssertEqual(noProject.rebaseSheet?.targets, .typed)
        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "no project, no branch list")

        let failing = makeModel(projectName: "proj")
        StubURLProtocol.enqueue(status: 503, body: #"{"detail":"Agent is offline"}"#)
        failing.openRebase()
        await waitUntil { failing.rebaseSheet?.targets != .loading }
        XCTAssertEqual(failing.rebaseSheet?.targets, .typed)
    }

    func testConsumeOpenRebaseOpensTheSheetOnce() async throws {
        let model = makeModel(projectName: nil, openRebase: true)
        XCTAssertTrue(model.openRebaseOnAppear)
        XCTAssertNil(model.rebaseSheet)

        model.consumeOpenRebase()

        XCTAssertNotNil(model.rebaseSheet)
        XCTAssertFalse(model.openRebaseOnAppear)
        await waitUntil { model.rebaseSheet?.targets == .typed }

        model.closeRebase()
        model.consumeOpenRebase()
        XCTAssertNil(model.rebaseSheet, "the terminal menu's Rebase… opens the sheet once")

        let plain = makeModel(projectName: nil)
        plain.consumeOpenRebase()
        XCTAssertNil(plain.rebaseSheet)
    }

    // MARK: - Start

    func testStartRebasePostsTheTrimmedBaseClosesTheSheetToastsAndReloads() async throws {
        let model = try await loadedModel("linked_worktree_full")
        await openTypedSheet(model)
        StubURLProtocol.enqueue(status: 200, body: try rebaseBody("rebased"))
        StubURLProtocol.enqueue(status: 200, body: try statusBody("unchanged"))

        await model.startRebase(onto: " origin/main \n")

        XCTAssertEqual(requestURLs.first, gitURL("rebase"))
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "POST")
        XCTAssertEqual(try sentBody(GitRebaseRequest.self, at: 0), GitRebaseRequest(onto: "origin/main"))
        XCTAssertEqual(requestURLs.count, 2, "the status is re-read after the answer")
        XCTAssertNil(model.rebaseSheet)
        XCTAssertEqual(model.toast?.text, "Rebased feature/x onto origin/main.")
        XCTAssertEqual(model.toast?.tone, .ok)
        XCTAssertFalse(model.isMutating)
    }

    func testAStopIsADangerToastOnTheCommitItStoppedAt() async throws {
        let model = try await loadedModel("linked_worktree_full")
        await openTypedSheet(model)
        StubURLProtocol.enqueue(status: 200, body: try rebaseBody("stopped_on_conflict"))
        StubURLProtocol.enqueue(status: 200, body: try statusBody("unchanged"))

        await model.startRebase(onto: "main")

        XCTAssertNil(model.rebaseSheet)
        XCTAssertEqual(model.toast?.text, "Rebase stopped at “Fix the login redirect” — resolve the conflicts, then Continue.")
        XCTAssertEqual(model.toast?.tone, .danger)
    }

    func testABlankBaseOrAClosedSheetSendsNothing() async throws {
        let model = try await loadedModel("linked_worktree_full")

        await model.startRebase(onto: "main")
        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "no sheet, no start")

        await openTypedSheet(model)
        await model.startRebase(onto: "  \n")
        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "a blank base is never sent")
        XCTAssertEqual(model.rebaseSheet?.isStarting, false)
    }

    func testARefusedStartStaysInTheSheetWithTheRebaseCopyAndGitsOutput() async throws {
        let model = try await loadedModel("linked_worktree_full")
        await openTypedSheet(model)
        StubURLProtocol.enqueue(
            status: 409,
            body: #"{"detail":{"code":"dirty_worktree","message":"error: cannot rebase: You have unstaged changes."}}"#
        )
        StubURLProtocol.enqueue(status: 200, body: try statusBody("unchanged"))

        await model.startRebase(onto: "main")

        XCTAssertEqual(
            model.rebaseSheet?.error,
            CommitError(
                message: "Local changes are in the way — stage, commit or discard them first.",
                output: "error: cannot rebase: You have unstaged changes."
            )
        )
        XCTAssertEqual(model.rebaseSheet?.isStarting, false)
        XCTAssertNil(model.toast)
        XCTAssertFalse(model.isMutating)
        XCTAssertEqual(requestURLs.count, 2, "the status is re-read even after a refusal")

        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"unknown_ref","message":"mian"}}"#)
        await model.startRebase(onto: "mian")
        XCTAssertEqual(model.rebaseSheet?.error, CommitError(message: "“mian” isn't a branch, tag or commit here.", output: nil))
    }

    func testCloseRebaseIsRefusedWhileStarting() async throws {
        let model = makeModel(projectName: nil)
        await openTypedSheet(model)
        model.rebaseSheet?.isStarting = true

        model.closeRebase()

        XCTAssertNotNil(model.rebaseSheet)
    }

    // MARK: - Continue / Skip / Abort

    func testEachStepPostsToItsOwnLeafWithNoBodyAndNamesTheRebasedBranch() async throws {
        // HEAD is detached mid-rebase; the branch comes from `repo.rebase`.
        let model = try await loadedModel("rebasing_stopped_on_conflict")
        StubURLProtocol.enqueue(status: 200, body: try rebaseBody("rebased"))
        StubURLProtocol.enqueue(status: 200, body: try statusBody("unchanged"))

        await model.continueRebase()

        XCTAssertEqual(requestURLs.first, gitURL("rebase/continue"))
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "POST")
        XCTAssertEqual(StubURLProtocol.bodies.first?.isEmpty, true)
        XCTAssertEqual(model.toast?.text, "Rebase finished.")

        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: try rebaseBody("aborted"))
        StubURLProtocol.enqueue(status: 200, body: try statusBody("unchanged"))
        model.requestRebaseStep(.abort)
        await model.confirmRebaseStep()

        XCTAssertEqual(requestURLs.first, gitURL("rebase/abort"))
        XCTAssertEqual(model.toast?.text, "Rebase aborted — topic is back where it started.")
        XCTAssertEqual(model.toast?.tone, .ok)

        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: try rebaseBody("rebased"))
        model.requestRebaseStep(.skip)
        await model.confirmRebaseStep()

        XCTAssertEqual(requestURLs.first, gitURL("rebase/skip"))
        XCTAssertFalse(model.isMutating)
    }

    func testTheConfirmStillHasItsStepAfterTheDialogBindingClearedThePrompt() async throws {
        // SwiftUI writes `isPresented` false before the confirm action runs:
        // the binding clears `rebasePrompt` alone, never the pending step.
        let model = try await loadedModel("rebasing_stopped_on_conflict")
        model.requestRebaseStep(.skip)
        XCTAssertEqual(model.rebasePrompt?.title, "Skip this commit?")
        XCTAssertEqual(model.rebasePrompt?.body, "fedcba9 “Fix the login redirect” will be left out of topic.")
        StubURLProtocol.enqueue(status: 200, body: try rebaseBody("rebased"))

        model.rebasePrompt = nil
        await model.confirmRebaseStep()

        XCTAssertEqual(requestURLs.first, gitURL("rebase/skip"))
    }

    func testADismissedPromptSendsNothing() async throws {
        let model = try await loadedModel("rebasing_stopped_on_conflict")
        model.requestRebaseStep(.abort)
        XCTAssertEqual(model.rebasePrompt?.confirm, "Abort rebase")

        model.dismissRebaseStep()
        await model.confirmRebaseStep()

        XCTAssertNil(model.rebasePrompt)
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
    }

    func testARefusedStepIsADangerToastWithTheRebaseCopyAndStillReloads() async throws {
        let model = try await loadedModel("rebasing_stopped_on_conflict")
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"unmerged","message":"unmerged paths in the index"}}"#)
        StubURLProtocol.enqueue(status: 200, body: try statusBody("unchanged"))

        await model.continueRebase()

        XCTAssertEqual(model.toast?.text, "Resolve the conflicts first.")
        XCTAssertEqual(model.toast?.tone, .danger)
        XCTAssertEqual(requestURLs.count, 2)
        XCTAssertFalse(model.isMutating)

        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"no_rebase","message":"clean"}}"#)
        await model.continueRebase()
        XCTAssertEqual(model.toast?.text, "No rebase is in progress anymore.")
    }

    // MARK: - Mark resolved

    func testResolveStagesTheConflictedPathAndAMarkerRefusalNamesTheFile() async throws {
        let model = try await loadedModel("rebasing_stopped_on_conflict")
        let conflicted = try XCTUnwrap(model.sections.flatMap(\.rows).first { $0.entry.group == GitStatusEntry.groupConflicts })
        XCTAssertEqual(GitChangesDisplay.rowActions(conflicted.entry, capabilities: model.capabilities).resolve, true)
        StubURLProtocol.enqueue(status: 200, body: #"{"ok":true}"#)
        StubURLProtocol.enqueue(status: 200, body: try statusBody("unchanged"))

        await model.resolve(conflicted)

        XCTAssertEqual(requestURLs.first, gitURL("stage"))
        XCTAssertEqual(try sentBody(GitPathsRequest.self, at: 0), GitPathsRequest(paths: ["src/login.py"]))
        XCTAssertNil(model.toast)

        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"conflict_markers","message":"src/login.py"}}"#)
        StubURLProtocol.enqueue(status: 200, body: try statusBody("unchanged"))
        await model.resolve(conflicted)

        XCTAssertEqual(model.toast?.text, "src/login.py still has conflict markers — finish resolving it first.")
        XCTAssertEqual(model.toast?.tone, .danger)
    }

    // MARK: - Conflict resolver

    private let resolverGit = #"{"changes":true,"stage":true,"discard":true,"commit":true,"rebase":true,"resolve":true,"conflicts":true}"#

    func testAConflictRowOpensTheResolverOnlyOnAnAgentThatServesIt() async throws {
        let withResolver = try await loadedModel("rebasing_stopped_on_conflict", git: resolverGit)
        let without = try await loadedModel("rebasing_stopped_on_conflict")
        let conflicted = try XCTUnwrap(withResolver.sections.flatMap(\.rows).first { $0.entry.group == GitStatusEntry.groupConflicts })

        XCTAssertTrue(withResolver.opensResolver(conflicted.entry))
        XCTAssertFalse(without.opensResolver(conflicted.entry))
        XCTAssertNil(withResolver.conflictTarget)

        withResolver.openResolver(for: conflicted.entry)

        XCTAssertEqual(withResolver.conflictTarget, GitConflictTarget(sessionId: "s1", path: "src/login.py"))
    }

    func testResolvedPathsComeFromTheStatusOnlyOnAnAgentWithTheResolver() async throws {
        let withResolver = try await loadedModel("rebasing_with_resolved_paths", git: resolverGit)
        let without = try await loadedModel("rebasing_with_resolved_paths")
        let plain = try await loadedModel("rebasing_stopped_on_conflict", git: resolverGit)

        XCTAssertEqual(withResolver.resolvedPaths, ["docs/notes with space.md", "src/session.py"])
        XCTAssertEqual(without.resolvedPaths, [], "no Restore that would 404")
        XCTAssertEqual(plain.resolvedPaths, [])
    }

    func testTheResolverClosingShowsItsOutcomeAndReReadsTheList() async throws {
        let model = try await loadedModel("rebasing_stopped_on_conflict", git: resolverGit)
        let conflicted = try XCTUnwrap(model.sections.flatMap(\.rows).first)
        model.openResolver(for: conflicted.entry)
        StubURLProtocol.enqueue(status: 200, body: try statusBody("rebasing_with_resolved_paths"))

        await model.resolverClosed(message: "Resolved src/login.py.")

        XCTAssertNil(model.conflictTarget)
        XCTAssertEqual(model.toast?.text, "Resolved src/login.py.")
        XCTAssertEqual(model.toast?.tone, .ok)
        XCTAssertEqual(requestURLs.count, 1)
        XCTAssertTrue(requestURLs[0].hasPrefix(gitURL("status")))
        XCTAssertEqual(model.resolvedPaths, ["docs/notes with space.md", "src/session.py"])
    }

    func testAResolverThatWasJustLeftSaysNothing() async throws {
        let model = try await loadedModel("rebasing_stopped_on_conflict", git: resolverGit)
        let conflicted = try XCTUnwrap(model.sections.flatMap(\.rows).first)
        model.openResolver(for: conflicted.entry)
        StubURLProtocol.enqueue(status: 200, body: try statusBody("unchanged"))

        await model.resolverClosed(message: nil)

        XCTAssertNil(model.conflictTarget)
        XCTAssertNil(model.toast)
    }

    func testRestoreWaitsForItsConfirmationThenRebuildsTheConflictAndReReadsTheList() async throws {
        let model = try await loadedModel("rebasing_with_resolved_paths", git: resolverGit)

        model.requestRestore("src/session.py")

        XCTAssertEqual(model.restorePrompt, "src/session.py")
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)

        StubURLProtocol.enqueue(status: 200, body: #"{"ok":true}"#)
        StubURLProtocol.enqueue(status: 200, body: try statusBody("rebasing_stopped_on_conflict"))
        await model.confirmRestore("src/session.py")

        XCTAssertEqual(requestURLs.first, gitURL("conflict/restore"))
        XCTAssertEqual(
            try sentBody(GitConflictRestoreRequest.self, at: 0),
            GitConflictRestoreRequest(path: "src/session.py", style: GitConflictRestoreRequest.styleMerge, token: nil)
        )
        XCTAssertEqual(model.toast?.text, "Restored the conflict in src/session.py.")
        XCTAssertEqual(model.toast?.tone, .ok)
        XCTAssertEqual(requestURLs.count, 2)
        XCTAssertTrue(requestURLs[1].hasPrefix(gitURL("status")))
        XCTAssertEqual(model.resolvedPaths, [])
    }

    func testARefusedRestoreShowsItsCopyAndReReadsTheListAnyway() async throws {
        let model = try await loadedModel("rebasing_with_resolved_paths", git: resolverGit)
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"not_restorable","message":"src/session.py"}}"#)
        StubURLProtocol.enqueue(status: 200, body: try statusBody("unchanged"))

        await model.confirmRestore("src/session.py")

        XCTAssertEqual(model.toast?.text, "The conflict in src/session.py can no longer be rebuilt.")
        XCTAssertEqual(model.toast?.tone, .danger)
        XCTAssertEqual(requestURLs.count, 2)
    }

    func testTheBannerInputsComeFromTheLoadedStatus() async throws {
        let model = try await loadedModel("rebasing_stopped_on_conflict")

        XCTAssertEqual(model.conflictCount, 1)
        XCTAssertEqual(
            GitChangesDisplay.rebaseBanner(model.repo, conflicts: model.conflictCount)?.title,
            "Rebasing topic onto origin/main"
        )
        XCTAssertEqual(model.status?.repo?.rebase?.step, 2)
    }
}
