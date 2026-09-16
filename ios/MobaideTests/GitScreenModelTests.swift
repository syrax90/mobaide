import XCTest
@testable import Mobaide

@MainActor
final class GitScreenModelTests: XCTestCase {
    private let gitItemsBody = #"{"items":[{"kind":"branch_only","branch":"b"}]}"#
    private let twoBranchesBody = #"{"items":[{"kind":"branch_only","branch":"b1"},{"kind":"branch_only","branch":"b2"}]}"#

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeModel(sleeper: any Sleeper) -> (GitScreenModel, AgentEventBus, FakeWebSocketTransportFactory) {
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
        let model = GitScreenModel(projectName: "proj", agentAPI: agentAPI, agentEventBus: bus, sleeper: sleeper)
        return (model, bus, factory)
    }

    private func waitUntil(_ predicate: @escaping () -> Bool) async {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if predicate() { return }
            try? await Task.sleep(for: .milliseconds(2))
        }
    }

    private func sentItems(inBodyAt index: Int) throws -> [JSONValue] {
        let json = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[index])
        return json["items"]?.arrayValue ?? []
    }

    private func forceValue(inBodyAt index: Int) throws -> JSONValue? {
        try sentItems(inBodyAt: index).first?["force"]
    }

    func testDeferredDeleteCommitsOnlyAfterWindow() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: gitItemsBody)
        await model.load()
        XCTAssertEqual(model.items.count, 1)

        model.select(model.items[0])
        XCTAssertEqual(model.selected, [model.items[0].id])

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b","ok":true}]}"#)
        model.requestDelete()
        XCTAssertNotNil(model.undoToast)
        XCTAssertTrue(model.visibleItems.isEmpty, "the queued row hides at once")
        XCTAssertTrue(model.selected.isEmpty)

        await waitUntil { sleeper.durations.contains(.seconds(5)) }
        XCTAssertEqual(StubURLProtocol.requests.count, 1, "the delete must not fire during the 5s window")

        sleeper.release()
        await waitUntil { StubURLProtocol.requests.count == 2 }
        let deleteRequest = StubURLProtocol.requests[1]
        XCTAssertEqual(deleteRequest.httpMethod, "POST")
        XCTAssertEqual(deleteRequest.url?.absoluteString.hasSuffix("/git/items:delete"), true)
        XCTAssertNil(model.undoToast, "the toast clears once the window elapses")
    }

    func testUndoCancelsDeferredDelete() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: gitItemsBody)
        await model.load()
        model.select(model.items[0])

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b","ok":true}]}"#)
        model.requestDelete()
        await waitUntil { sleeper.durations.contains(.seconds(5)) }

        model.undo()
        XCTAssertNil(model.undoToast)
        XCTAssertEqual(model.visibleItems.count, 1, "undo restores the hidden row")
        sleeper.release()
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(StubURLProtocol.requests.count, 1, "undo cancels the pending delete entirely — nothing is sent")
    }

    func testSwipeDeleteHidesRowAndSendsAfterWindow() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: gitItemsBody)
        await model.load()

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b","ok":true}]}"#)
        model.delete(model.items[0])
        XCTAssertTrue(model.visibleItems.isEmpty)
        XCTAssertEqual(model.undoToast?.message, "Deleting 1 item(s)…")

        await waitUntil { sleeper.durations.contains(.seconds(5)) }
        XCTAssertEqual(StubURLProtocol.requests.count, 1)

        sleeper.release()
        await waitUntil { StubURLProtocol.requests.count == 2 }
        XCTAssertEqual(try sentItems(inBodyAt: 1).count, 1)
    }

    func testSecondDeleteInsideWindowIsCoalescedIntoOneRequest() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: twoBranchesBody)
        await model.load()

        model.delete(model.items[0])
        await waitUntil { sleeper.durations.count == 1 }
        model.delete(model.items[1])
        await waitUntil { sleeper.durations.count == 2 }

        XCTAssertEqual(model.undoToast?.message, "Deleting 2 item(s)…")
        XCTAssertTrue(model.visibleItems.isEmpty)

        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"results":[{"kind":"branch_only","target":"b1","ok":true},{"kind":"branch_only","target":"b2","ok":true}]}"#
        )
        sleeper.release()
        await waitUntil { StubURLProtocol.requests.count == 2 }
        try await Task.sleep(for: .milliseconds(20))

        XCTAssertEqual(StubURLProtocol.requests.count, 2, "the restarted window must not drop the first batch into a second POST")
        let sent = try sentItems(inBodyAt: 1)
        XCTAssertEqual(sent.compactMap { $0["branch"]?.stringValue }, ["b1", "b2"])
    }

    func testUndoRestoresEveryCoalescedRow() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: twoBranchesBody)
        await model.load()

        model.delete(model.items[0])
        await waitUntil { sleeper.durations.count == 1 }
        model.delete(model.items[1])
        await waitUntil { sleeper.durations.count == 2 }

        model.undo()
        XCTAssertEqual(model.visibleItems.count, 2)
        sleeper.release()
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(StubURLProtocol.requests.count, 1)
    }

    func testPartialFailureRestoresOnlyTheFailedRow() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: twoBranchesBody)
        await model.load()

        model.delete(model.items[0])
        await waitUntil { sleeper.durations.count == 1 }
        model.delete(model.items[1])
        await waitUntil { sleeper.durations.count == 2 }

        StubURLProtocol.enqueue(
            status: 200,
            body: #"""
            {"results":[{"kind":"branch_only","target":"b1","ok":true},{"kind":"branch_only","target":"b2","ok":false,"error":"locked"}]}
            """#
        )
        sleeper.release()
        await waitUntil { model.message != nil }

        XCTAssertEqual(model.visibleItems.map(\.branch), ["b2"], "only the failed row comes back")
        XCTAssertEqual(model.message, "Couldn't delete b2: the worktree is locked")
    }

    func testForceRequiredPromptsThenResendsWithForce() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: gitItemsBody)
        await model.load()
        model.select(model.items[0])

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b","ok":false,"error":"force_required"}]}"#)
        model.requestDelete()
        await waitUntil { sleeper.durations.contains(.seconds(5)) }
        sleeper.release()

        await waitUntil { model.forcePrompt != nil }
        XCTAssertEqual(model.forcePrompt?.items.count, 1)
        XCTAssertEqual(model.visibleItems.count, 1, "nothing was deleted, so the row is visible under the prompt")
        let firstForce = try forceValue(inBodyAt: 1)
        XCTAssertEqual(firstForce, .bool(false), "the first attempt is sent without force")

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b","ok":true}]}"#)
        model.confirmForceDelete()
        XCTAssertTrue(model.visibleItems.isEmpty, "confirming re-hides the row")
        await waitUntil { StubURLProtocol.requests.count == 3 }

        let resendForce = try forceValue(inBodyAt: 2)
        XCTAssertEqual(resendForce, .bool(true), "the same item is re-sent with force=true")
        XCTAssertNil(model.forcePrompt, "the prompt clears after confirming")
    }

    /// The confirm action must survive SwiftUI clearing `forcePrompt` when the
    /// dialog dismisses, which can land before the button action runs.
    func testForceConfirmSurvivesPromptClearedByDismissal() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: gitItemsBody)
        await model.load()

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b","ok":false,"error":"force_required"}]}"#)
        model.delete(model.items[0])
        await waitUntil { sleeper.durations.contains(.seconds(5)) }
        sleeper.release()
        await waitUntil { model.forcePrompt != nil }

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b","ok":true}]}"#)
        model.forcePrompt = nil
        model.confirmForceDelete()
        await waitUntil { StubURLProtocol.requests.count == 3 }
        XCTAssertEqual(try forceValue(inBodyAt: 2), .bool(true))
    }

    func testForcePromptCancelLeavesTheRowAndSendsNothing() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: gitItemsBody)
        await model.load()

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b","ok":false,"error":"force_required"}]}"#)
        model.delete(model.items[0])
        await waitUntil { sleeper.durations.contains(.seconds(5)) }
        sleeper.release()
        await waitUntil { model.forcePrompt != nil }

        model.dismissForcePrompt()
        model.confirmForceDelete()
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(model.visibleItems.count, 1)
        XCTAssertEqual(StubURLProtocol.requests.count, 2, "cancelling must not re-send")
    }

    func testForcePromptMapsPositionallyInACoalescedBatch() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"items":[{"kind":"branch_only","branch":"b1"},{"kind":"branch_only","branch":"b2"},{"kind":"branch_only","branch":"b3"}]}"#
        )
        await model.load()

        model.delete(model.items[0])
        await waitUntil { sleeper.durations.count == 1 }
        model.delete(model.items[1])
        await waitUntil { sleeper.durations.count == 2 }
        model.delete(model.items[2])
        await waitUntil { sleeper.durations.count == 3 }

        StubURLProtocol.enqueue(
            status: 200,
            body: #"""
            {"results":[{"kind":"branch_only","target":"b1","ok":true},{"kind":"branch_only","target":"b2","ok":false,"error":"force_required"},{"kind":"branch_only","target":"b3","ok":true}]}
            """#
        )
        sleeper.release()
        await waitUntil { model.forcePrompt != nil }
        XCTAssertEqual(model.forcePrompt?.refs, ["b2"])

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b2","ok":true}]}"#)
        model.confirmForceDelete()
        await waitUntil { StubURLProtocol.requests.count == 3 }
        let resent = try sentItems(inBodyAt: 2)
        XCTAssertEqual(resent.count, 1)
        XCTAssertEqual(resent.first?["branch"]?.stringValue, "b2")
        XCTAssertEqual(resent.first?["force"], .bool(true))
    }

    func testBusyConflictPopulatesHoldersAndRestoresEveryRow() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: gitItemsBody)
        await model.load()
        model.select(model.items[0])

        StubURLProtocol.enqueue(
            status: 409,
            body: #"{"detail":{"busy_by":[{"kind":"session","session_id":"s1","cwd":"/w"}]}}"#
        )
        model.requestDelete()
        await waitUntil { sleeper.durations.contains(.seconds(5)) }
        sleeper.release()

        await waitUntil { model.busyDialog != nil }
        XCTAssertEqual(model.busyDialog?.count, 1)
        XCTAssertEqual(model.busyDialog?.first?.kind, "session")
        XCTAssertEqual(model.busyDialog?.first?.session_id, "s1")
        XCTAssertEqual(model.visibleItems.count, 1, "409 rejects the whole batch, so every row comes back")
    }

    func testCurrentBranchIsNeitherSelectableNorDeletable() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: #"{"items":[{"kind":"branch_only","branch":"main","is_current":true}]}"#)
        await model.load()

        model.select(model.items[0])
        XCTAssertTrue(model.selected.isEmpty)
        model.delete(model.items[0])
        XCTAssertNil(model.undoToast)
        XCTAssertEqual(model.visibleItems.count, 1)
        XCTAssertFalse(model.hasDeletableItems)
    }

    func testHiddenRowsArePrunedOnReload() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: twoBranchesBody)
        await model.load()

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b1","ok":true}]}"#)
        model.delete(model.items[0])
        await waitUntil { sleeper.durations.contains(.seconds(5)) }
        sleeper.release()
        await waitUntil { StubURLProtocol.requests.count == 2 }

        StubURLProtocol.enqueue(status: 200, body: #"{"items":[{"kind":"branch_only","branch":"b2"}]}"#)
        await model.load()
        XCTAssertEqual(model.visibleItems.map(\.branch), ["b2"])

        // The branch is re-created upstream: the stale hidden id must not keep it invisible.
        StubURLProtocol.enqueue(status: 200, body: twoBranchesBody)
        await model.load()
        XCTAssertEqual(model.visibleItems.count, 2)
    }

    func testSelectionIsPrunedWhenARowDisappears() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: twoBranchesBody)
        await model.load()
        model.select(model.items[0])
        model.select(model.items[1])
        XCTAssertEqual(model.selected.count, 2)

        StubURLProtocol.enqueue(status: 200, body: #"{"items":[{"kind":"branch_only","branch":"b2"}]}"#)
        await model.load()
        XCTAssertEqual(model.selected, [model.items[0].id])
    }

    func testGitChangedTriggersReload() async throws {
        let sleeper = ManualSleeper()
        let (model, bus, factory) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: gitItemsBody)
        await model.load()
        XCTAssertEqual(model.items.count, 1)

        Task { await model.observeGitChanged() }
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        StubURLProtocol.enqueue(status: 200, body: #"{"items":[{"kind":"branch_only","branch":"b"},{"kind":"branch_only","branch":"c"}]}"#)
        connection.emit(.text(#"{"type":"git.changed","payload":{"project_name":"proj"}}"#))

        await waitUntil { model.items.count == 2 }
        XCTAssertEqual(model.items.map(\.branch), ["b", "c"])
    }

    // MARK: - Branches: capability, create, check out

    private let versionURL = "http://agent.example.com/api/version"
    private let itemsURL = "http://agent.example.com/api/projects/proj/git/items?remote=true"
    private let branchesBody = #"""
    {"items":[{"kind":"branch_only","branch":"main","is_current":true},{"kind":"branch_only","branch":"stale"},{"kind":"branch_with_worktree","branch":"topic","worktree_path":"/repo/wt/topic"},{"kind":"remote_branch","branch":"origin/feature-x","remote":"origin","subject":"Add x","committed_at":"2026-09-16T10:20:30+02:00"}]}
    """#
    private let dirtyCopy = "The project folder has local changes this checkout would overwrite. "
        + "Commit or discard them from a session's Changes screen first."

    private var requestURLs: [String] {
        StubURLProtocol.requests.compactMap { $0.url?.absoluteString }
    }

    private func versionBody(_ git: String) -> String {
        #"{"name":"mobaide-agent","server_version":"1.4.0","protocol_versions":[7],"min_client_version":1,"capabilities":{"git":\#(git)}}"#
    }

    /// The four-row list on screen with the capability on, the probe's and the
    /// load's traffic already forgotten.
    private func branchesModel() async throws -> GitScreenModel {
        let (model, _, _) = makeModel(sleeper: ManualSleeper())
        StubURLProtocol.enqueue(status: 200, body: versionBody(#"{"branches":true}"#))
        await model.loadCapabilities()
        StubURLProtocol.enqueue(status: 200, body: branchesBody)
        await model.load()
        StubURLProtocol.reset()
        return model
    }

    private func row(_ model: GitScreenModel, _ branch: String) throws -> GitItem {
        try XCTUnwrap(model.items.first { $0.branch == branch }, "no row \(branch)")
    }

    private func sentBody<T: Decodable>(_ type: T.Type, at index: Int) throws -> T {
        let bodies = StubURLProtocol.bodies
        let data = try XCTUnwrap(index < bodies.count ? bodies[index] : nil, "no body at \(index)")
        return try WireJSON.decoder().decode(type, from: data)
    }

    func testLoadIsOneGetThatAlwaysAsksForRemoteRows() async throws {
        let (model, _, _) = makeModel(sleeper: ManualSleeper())
        StubURLProtocol.enqueue(status: 200, body: branchesBody)

        await model.load()

        XCTAssertEqual(requestURLs, [itemsURL])
        XCTAssertEqual(model.localItems.map(\.branch), ["main", "stale", "topic"])
        XCTAssertEqual(model.remoteItems.map(\.branch), ["origin/feature-x"])
        XCTAssertFalse(model.remoteOpen)
        XCTAssertFalse(model.filterable)
    }

    func testLoadCapabilitiesIsItsOwnRequestAndGatesTheBranchActions() async throws {
        let (model, _, _) = makeModel(sleeper: ManualSleeper())
        StubURLProtocol.enqueue(status: 200, body: branchesBody)
        await model.load()
        XCTAssertFalse(model.canManageBranches)
        model.openNewBranch()
        XCTAssertNil(model.newBranch)
        model.requestCheckout(try row(model, "stale"))
        XCTAssertNil(model.checkoutPrompt)

        StubURLProtocol.enqueue(status: 200, body: versionBody(#"{"branches":true}"#))
        await model.loadCapabilities()

        XCTAssertEqual(requestURLs, [itemsURL, versionURL])
        XCTAssertTrue(model.canManageBranches)
        model.openNewBranch()
        XCTAssertEqual(model.newBranch, GitScreenModel.NewBranchSheetState())
    }

    func testAFailedCapabilityProbeKeepsTheDeleteOnlyScreen() async throws {
        let (model, _, _) = makeModel(sleeper: ManualSleeper())
        StubURLProtocol.enqueue(status: 503, body: #"{"detail":"Agent is offline"}"#)

        await model.loadCapabilities()

        XCTAssertFalse(model.canManageBranches)
    }

    func testCreateBranchPostsTheTrimmedFormClosesTheSheetAndReportsTheResult() async throws {
        let model = try await branchesModel()
        model.openNewBranch()
        StubURLProtocol.enqueue(status: 200, body: #"{"branch":"feature/new","checked_out":true,"created":true}"#)

        await model.createBranch(name: "  feature/new ", startPoint: "main", checkout: true)

        XCTAssertEqual(requestURLs, ["http://agent.example.com/api/projects/proj/git/branches"])
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "POST")
        XCTAssertEqual(
            try sentBody(GitBranchCreateRequest.self, at: 0),
            GitBranchCreateRequest(name: "feature/new", start_point: "main", checkout: true)
        )
        XCTAssertNil(model.newBranch)
        XCTAssertEqual(model.message, "Created and checked out feature/new")
    }

    func testCreateBranchIgnoresABlankNameAndAClosedSheet() async throws {
        let model = try await branchesModel()

        await model.createBranch(name: "feature/new", startPoint: nil, checkout: false)
        model.openNewBranch()
        await model.createBranch(name: "   ", startPoint: nil, checkout: false)

        XCTAssertEqual(requestURLs, [])
        XCTAssertEqual(model.newBranch, GitScreenModel.NewBranchSheetState())
    }

    func testCreateBranchExistsKeepsTheSheetOpenWithTheRefusalInline() async throws {
        let model = try await branchesModel()
        model.openNewBranch()
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"exists","message":"feature/new"}}"#)

        await model.createBranch(name: "feature/new", startPoint: nil, checkout: false)

        XCTAssertEqual(
            model.newBranch,
            GitScreenModel.NewBranchSheetState(isCreating: false, error: "A branch named feature/new already exists.")
        )
        XCTAssertNil(model.message)
        model.closeNewBranch()
        XCTAssertNil(model.newBranch)
    }

    func testCreateBranchBusyClosesTheSheetAndShowsTheHolders() async throws {
        let model = try await branchesModel()
        model.openNewBranch()
        StubURLProtocol.enqueue(
            status: 409,
            body: #"{"detail":{"busy_by":[{"kind":"session","session_id":"s1","cwd":"/repo"}]}}"#
        )

        await model.createBranch(name: "feature/new", startPoint: nil, checkout: true)

        XCTAssertNil(model.newBranch)
        XCTAssertEqual(model.busyDialog?.map(\.session_id), ["s1"])
    }

    func testRequestCheckoutPromptsOnlyForRowsNothingHasCheckedOut() async throws {
        let model = try await branchesModel()

        model.requestCheckout(try row(model, "main"))
        XCTAssertNil(model.checkoutPrompt, "the current branch")
        model.requestCheckout(try row(model, "topic"))
        XCTAssertNil(model.checkoutPrompt, "a worktree-backed branch")

        model.requestCheckout(try row(model, "origin/feature-x"))
        XCTAssertEqual(model.checkoutPrompt?.branch, "origin/feature-x")
        model.dismissCheckoutPrompt()
        XCTAssertNil(model.checkoutPrompt)

        model.requestCheckout(try row(model, "stale"))
        XCTAssertEqual(model.checkoutPrompt?.branch, "stale")
    }

    /// The confirm action must survive SwiftUI clearing `checkoutPrompt` when
    /// the dialog dismisses, which can land before the button action runs.
    func testConfirmCheckoutPostsTheRefAndSurvivesThePromptClearedByDismissal() async throws {
        let model = try await branchesModel()
        model.requestCheckout(try row(model, "origin/feature-x"))
        StubURLProtocol.enqueue(status: 200, body: #"{"branch":"feature-x","checked_out":true,"created":true}"#)

        model.checkoutPrompt = nil
        model.confirmCheckout()
        XCTAssertTrue(model.isCheckingOut)
        await waitUntil { model.message != nil }

        XCTAssertEqual(requestURLs, ["http://agent.example.com/api/projects/proj/git/checkout"])
        XCTAssertEqual(try sentBody(GitCheckoutRequest.self, at: 0), GitCheckoutRequest(ref: "origin/feature-x"))
        XCTAssertEqual(model.message, "Checked out feature-x")
        XCTAssertFalse(model.isCheckingOut)
    }

    func testConfirmCheckoutWithoutAPromptSendsNothing() async throws {
        let model = try await branchesModel()

        model.confirmCheckout()
        try await Task.sleep(for: .milliseconds(20))

        XCTAssertEqual(requestURLs, [])
        XCTAssertFalse(model.isCheckingOut)
    }

    func testCheckoutBusyShowsTheHolders() async throws {
        let model = try await branchesModel()
        model.requestCheckout(try row(model, "stale"))
        StubURLProtocol.enqueue(
            status: 409,
            body: #"{"detail":{"busy_by":[{"kind":"auto_scan","project_name":"proj","worktree_path":"/repo"}]}}"#
        )

        model.confirmCheckout()
        await waitUntil { model.busyDialog != nil }

        XCTAssertEqual(model.busyDialog?.first?.kind, "auto_scan")
        XCTAssertNil(model.message)
        XCTAssertFalse(model.isCheckingOut)
    }

    func testCheckoutDirtyShowsTheDirtyCopy() async throws {
        let model = try await branchesModel()
        model.requestCheckout(try row(model, "stale"))
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"dirty","message":"error: Your local changes"}}"#)

        model.confirmCheckout()
        await waitUntil { model.message != nil }

        XCTAssertEqual(model.message, dirtyCopy)
        XCTAssertNil(model.busyDialog)
        XCTAssertFalse(model.isCheckingOut)
    }

    func testAFilterAppearsAtEightRowsNarrowsBothSectionsAndOpensTheRemoteOne() async throws {
        let (model, _, _) = makeModel(sleeper: ManualSleeper())
        let locals = (1...6).map { #"{"kind":"branch_only","branch":"feature-\#($0)"}"# }
        let remotes = [
            #"{"kind":"remote_branch","branch":"origin/feature-1","remote":"origin"}"#,
            #"{"kind":"remote_branch","branch":"origin/main","remote":"origin"}"#,
        ]
        StubURLProtocol.enqueue(status: 200, body: #"{"items":[\#((locals + remotes).joined(separator: ","))]}"#)
        await model.load()
        XCTAssertTrue(model.filterable)
        XCTAssertFalse(model.remoteOpen)

        model.filter = " FEATURE-1 "
        XCTAssertEqual(model.localItems.map(\.branch), ["feature-1"])
        XCTAssertEqual(model.remoteItems.map(\.branch), ["origin/feature-1"])
        XCTAssertTrue(model.remoteOpen)

        model.filter = "   "
        XCTAssertEqual(model.localItems.count, 6)
        XCTAssertFalse(model.remoteOpen)
        model.toggleRemote()
        XCTAssertTrue(model.remoteOpen)
    }

    func testRemoteRowsAreNeitherSelectableNorDeletable() async throws {
        let model = try await branchesModel()
        let remote = try row(model, "origin/feature-x")

        model.select(remote)
        XCTAssertTrue(model.selected.isEmpty)
        model.delete(remote)

        XCTAssertNil(model.undoToast)
        XCTAssertEqual(model.visibleItems.count, 4)
    }
}
