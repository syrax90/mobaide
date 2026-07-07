import XCTest
@testable import Mobaide

@MainActor
final class ProjectsRepositoryTests: XCTestCase {
    private struct StubAgentTokens: AgentTokenProviding {
        func validToken() async -> AgentToken? { nil }
        func refreshedToken() async -> String? { nil }
    }

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeStack() -> (ProjectsRepository, AgentEventBus, FakeWebSocketTransportFactory) {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let factory = FakeWebSocketTransportFactory()
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
        let repo = ProjectsRepository(agentAPI: agentAPI, agentEventBus: bus)
        return (repo, bus, factory)
    }

    private func makeRESTRepo() -> ProjectsRepository {
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
        return ProjectsRepository(agentAPI: agentAPI, agentEventBus: bus)
    }

    private func project(_ name: String) -> String {
        #"{"name":"\#(name)","path":"/\#(name)"}"#
    }

    private func projectsChanged(_ names: [String]) -> String {
        let payload = names.map { project($0) }.joined(separator: ",")
        return #"{"type":"projects.changed","payload":{"projects":[\#(payload)]}}"#
    }

    private func waitFor(
        _ repo: ProjectsRepository,
        _ predicate: @escaping ([ProjectInfo]) -> Bool
    ) async -> [ProjectInfo] {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if predicate(repo.currentProjects) { return repo.currentProjects }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return repo.currentProjects
    }

    func testProjectsChangedReplacesSnapshot() async throws {
        let (repo, bus, factory) = makeStack()
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        connection.emit(.text(projectsChanged(["a", "b"])))
        var list = await waitFor(repo) { $0.count == 2 }
        XCTAssertEqual(list.map(\.name), ["a", "b"])

        connection.emit(.text(projectsChanged(["c"])))
        list = await waitFor(repo) { $0.map(\.name) == ["c"] }
        XCTAssertEqual(list.map(\.name), ["c"], "changed snapshot replaces the whole list")
    }

    func testStreamMirrorsCurrentProjects() async throws {
        let (repo, bus, factory) = makeStack()
        let collector = StreamCollector(repo.projects())
        repo.start()
        await bus.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        connection.emit(.text(projectsChanged(["a"])))

        let hasOne: @Sendable ([ProjectInfo]) -> Bool = { $0.count == 1 }
        let snapshot = try await collector.next(where: hasOne)
        XCTAssertEqual(snapshot.map(\.name), ["a"])
    }

    func testSeedIfEmptyFetchesOnceThenServesCache() async {
        StubURLProtocol.enqueue(status: 200, body: #"[{"name":"a","path":"/a"},{"name":"b","path":"/b"}]"#)
        let repo = makeRESTRepo()

        let seeded = await repo.seedIfEmpty()
        XCTAssertEqual(seeded.map(\.name), ["a", "b"])
        XCTAssertEqual(repo.currentProjects.map(\.name), ["a", "b"])

        let again = await repo.seedIfEmpty()
        XCTAssertEqual(again.map(\.name), ["a", "b"])
        XCTAssertEqual(StubURLProtocol.requests.count, 1, "seed fetches only while the cache is empty")
    }

    func testCreateProjectMaps409ToProjectConflictError() async {
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":"Project 'p' already exists"}"#)
        let repo = makeRESTRepo()

        do {
            _ = try await repo.createProject(ProjectWrite(name: "p", path: "/x"))
            XCTFail("expected ProjectConflictError")
        } catch let error as ProjectConflictError {
            XCTAssertEqual(error.detail, "Project 'p' already exists")
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }
}
