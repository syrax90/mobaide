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
}
