import XCTest
@testable import Mobaide

@MainActor
final class GitScreenModelTests: XCTestCase {
    private let gitItemsBody = #"{"items":[{"kind":"branch_only","branch":"b"}]}"#

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

    private func forceValue(inBodyAt index: Int) throws -> JSONValue? {
        let json = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[index])
        return json["items"]?.arrayValue?.first?["force"]
    }

    func testDeferredDeleteCommitsOnlyAfterWindow() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: gitItemsBody)
        await model.load()
        XCTAssertEqual(model.items.count, 1)

        model.rowLongPressed(model.items[0])
        XCTAssertTrue(model.inSelectionMode)

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b","ok":true}]}"#)
        model.requestDelete()
        XCTAssertNotNil(model.undoToast)

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
        model.rowLongPressed(model.items[0])

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b","ok":true}]}"#)
        model.requestDelete()
        await waitUntil { sleeper.durations.contains(.seconds(5)) }

        model.undo()
        XCTAssertNil(model.undoToast)
        sleeper.release()
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(StubURLProtocol.requests.count, 1, "undo cancels the pending delete entirely — nothing is sent")
    }

    func testForceRequiredPromptsThenResendsWithForce() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: gitItemsBody)
        await model.load()
        model.rowLongPressed(model.items[0])

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b","ok":false,"error":"force_required"}]}"#)
        model.requestDelete()
        await waitUntil { sleeper.durations.contains(.seconds(5)) }
        sleeper.release()

        await waitUntil { model.forcePrompt != nil }
        XCTAssertEqual(model.forcePrompt?.items.count, 1)
        let firstForce = try forceValue(inBodyAt: 1)
        XCTAssertEqual(firstForce, .bool(false), "the first attempt is sent without force")

        StubURLProtocol.enqueue(status: 200, body: #"{"results":[{"kind":"branch_only","target":"b","ok":true}]}"#)
        model.confirmForceDelete()
        await waitUntil { StubURLProtocol.requests.count == 3 }

        let resendForce = try forceValue(inBodyAt: 2)
        XCTAssertEqual(resendForce, .bool(true), "the same item is re-sent with force=true")
        XCTAssertNil(model.forcePrompt, "the prompt clears after confirming")
    }

    func testBusyConflictPopulatesHolders() async throws {
        let sleeper = ManualSleeper()
        let (model, _, _) = makeModel(sleeper: sleeper)
        StubURLProtocol.enqueue(status: 200, body: gitItemsBody)
        await model.load()
        model.rowLongPressed(model.items[0])

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
