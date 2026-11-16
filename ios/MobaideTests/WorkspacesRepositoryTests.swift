import XCTest
@testable import Mobaide

@MainActor
final class WorkspacesRepositoryTests: XCTestCase {
    private struct StubAgentTokens: AgentTokenProviding {
        func validToken() async -> AgentToken? { nil }
        func refreshedToken() async -> String? { nil }
    }

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeStack() -> (WorkspacesRepository, AgentEventBus, FakeWebSocketTransportFactory) {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let factory = FakeWebSocketTransportFactory()
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
        let repo = WorkspacesRepository(agentAPI: agentAPI, agentEventBus: bus)
        return (repo, bus, factory)
    }

    private func makeRESTRepo() -> WorkspacesRepository {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: client
        )
        return WorkspacesRepository(agentAPI: agentAPI, agentEventBus: bus)
    }

    private func workspace(_ name: String) -> String {
        #"{"name":"\#(name)","path":"/\#(name)"}"#
    }

    private func workspacesChanged(_ names: [String]) -> String {
        let payload = names.map { workspace($0) }.joined(separator: ",")
        return #"{"type":"projects.changed","payload":{"projects":[\#(payload)]}}"#
    }

    private func waitFor(
        _ repo: WorkspacesRepository,
        _ predicate: @escaping ([WorkspaceInfo]) -> Bool
    ) async -> [WorkspaceInfo] {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if predicate(repo.currentWorkspaces) { return repo.currentWorkspaces }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return repo.currentWorkspaces
    }

    func testWorkspacesChangedReplacesSnapshot() async throws {
        let (repo, bus, factory) = makeStack()
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        connection.emit(.text(workspacesChanged(["a", "b"])))
        var list = await waitFor(repo) { $0.count == 2 }
        XCTAssertEqual(list.map(\.name), ["a", "b"])

        connection.emit(.text(workspacesChanged(["c"])))
        list = await waitFor(repo) { $0.map(\.name) == ["c"] }
        XCTAssertEqual(list.map(\.name), ["c"], "changed snapshot replaces the whole list")
    }

    func testSeedIfEmptyFetchesOnceThenServesCache() async {
        StubURLProtocol.enqueue(status: 200, body: #"[{"name":"a","path":"/a"},{"name":"b","path":"/b"}]"#)
        let repo = makeRESTRepo()

        let seeded = await repo.seedIfEmpty()
        XCTAssertEqual(seeded.map(\.name), ["a", "b"])
        XCTAssertEqual(repo.currentWorkspaces.map(\.name), ["a", "b"])

        let again = await repo.seedIfEmpty()
        XCTAssertEqual(again.map(\.name), ["a", "b"])
        XCTAssertEqual(StubURLProtocol.requests.count, 1, "seed fetches only while the cache is empty")
    }

    func testCreateWorkspaceMaps409ToWorkspaceConflictError() async {
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":"Workspace 'p' already exists"}"#)
        let repo = makeRESTRepo()

        do {
            _ = try await repo.createWorkspace(WorkspaceWrite(name: "p", path: "/x"))
            XCTFail("expected WorkspaceConflictError")
        } catch let error as WorkspaceConflictError {
            XCTAssertEqual(error.detail, "Workspace 'p' already exists")
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }
}
