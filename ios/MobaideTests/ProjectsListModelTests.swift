import XCTest
@testable import Mobaide

@MainActor
final class ProjectsListModelTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeStack(
        sleeper: any Sleeper
    ) -> (ProjectsListModel, ProjectsRepository, AgentEventBus, FakeWebSocketTransportFactory) {
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
        let repo = ProjectsRepository(agentAPI: agentAPI, agentEventBus: bus)
        repo.start()
        let model = ProjectsListModel(repository: repo, agentEventBus: bus, sleeper: sleeper)
        return (model, repo, bus, factory)
    }

    private func startObserving(_ model: ProjectsListModel) {
        Task { await model.observeProjects() }
    }

    private func feedConnected(
        _ bus: AgentEventBus,
        _ factory: FakeWebSocketTransportFactory
    ) async throws -> FakeWebSocketConnection {
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        return connection
    }

    private func projectsChanged(_ items: [(String, Int)]) -> String {
        let payload = items
            .map { #"{"name":"\#($0.0)","path":"/\#($0.0)","order":\#($0.1)}"# }
            .joined(separator: ",")
        return #"{"type":"projects.changed","payload":{"projects":[\#(payload)]}}"#
    }

    private func projectListBody(_ items: [(String, Int)]) -> String {
        "[" + items.map { #"{"name":"\#($0.0)","path":"/\#($0.0)","order":\#($0.1)}"# }.joined(separator: ",") + "]"
    }

    private func waitUntil(_ predicate: @escaping () -> Bool) async {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if predicate() { return }
            try? await Task.sleep(for: .milliseconds(2))
        }
    }

    func testReorderIsOptimisticThenReconcilesFromEvent() async throws {
        let (model, _, bus, factory) = makeStack(sleeper: RecordingSleeper())
        startObserving(model)
        let connection = try await feedConnected(bus, factory)
        connection.emit(.text(projectsChanged([("a", 0), ("b", 1), ("c", 2)])))
        await waitUntil { model.projects.map(\.name) == ["a", "b", "c"] }

        StubURLProtocol.enqueue(status: 200, body: projectListBody([("c", 0), ("a", 1), ("b", 2)]))
        model.move(from: IndexSet(integer: 2), to: 0)
        XCTAssertEqual(model.projects.map(\.name), ["c", "a", "b"], "optimistic order applied immediately")

        connection.emit(.text(projectsChanged([("c", 0), ("a", 1), ("b", 2)])))
        await waitUntil { model.projects.map(\.name) == ["c", "a", "b"] }

        // A later snapshot in a different order proves the pending override was
        // cleared (otherwise it would keep forcing c-a-b).
        connection.emit(.text(projectsChanged([("a", 0), ("b", 1), ("c", 2)])))
        await waitUntil { model.projects.map(\.name) == ["a", "b", "c"] }
        XCTAssertEqual(model.projects.map(\.name), ["a", "b", "c"], "pending order cleared after reconcile")
    }

    func testReorderFailureRevertsAndMessages() async throws {
        let (model, _, bus, factory) = makeStack(sleeper: RecordingSleeper())
        startObserving(model)
        let connection = try await feedConnected(bus, factory)
        connection.emit(.text(projectsChanged([("a", 0), ("b", 1), ("c", 2)])))
        await waitUntil { model.projects.map(\.name) == ["a", "b", "c"] }

        StubURLProtocol.enqueue(status: 500, body: "boom")
        model.move(from: IndexSet(integer: 0), to: 3)
        XCTAssertEqual(model.projects.map(\.name), ["b", "c", "a"], "optimistic order applied immediately")

        await waitUntil { model.message != nil }
        XCTAssertEqual(model.projects.map(\.name), ["a", "b", "c"], "reverts to authoritative order on failure")
        XCTAssertEqual(model.message, "Reorder failed — reverted.")
    }

    func testProjectUndoRecreatesAndRemindsAboutLinearKey() async {
        StubURLProtocol.enqueue(status: 200, body: "")
        StubURLProtocol.enqueue(status: 200, body: #"{"name":"x","path":"/x","has_linear_key":false}"#)
        let (model, _, _, _) = makeStack(sleeper: ManualSleeper())
        let target = ProjectInfo(name: "x", path: "/x", has_linear_key: true)

        model.delete(target)
        await waitUntil { model.undoToast != nil }
        XCTAssertNotNil(model.undoToast, "delete shows an undo snackbar")

        model.undo()
        await waitUntil { model.message != nil }
        XCTAssertEqual(model.message, "Restored — re-enter the Linear API key in Edit.")

        let methods = StubURLProtocol.requests.map { $0.httpMethod }
        XCTAssertEqual(methods, ["DELETE", "POST"], "undo re-creates via POST after the immediate DELETE")
    }

    func testBusyProjectDeleteSurfacesServerConflict() async {
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":"Project 'x' has an active auto-scan."}"#)
        let (model, _, _, _) = makeStack(sleeper: ManualSleeper())

        model.delete(ProjectInfo(name: "x", path: "/x"))
        await waitUntil { model.message != nil }
        XCTAssertEqual(model.message, "Project 'x' has an active auto-scan.")
        XCTAssertNil(model.undoToast, "a refused delete shows no undo snackbar")
    }
}
