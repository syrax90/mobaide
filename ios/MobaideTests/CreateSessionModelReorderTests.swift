import XCTest
@testable import Mobaide

/// Reordering in the create-session wizard: the project list saves the
/// agent's own project order (`PATCH /api/projects/order`), the mode list
/// saves the account's mode order through `CustomAgentTypesRepository`. Both
/// show a move at once; a failed save reverts and says so. Also pins
/// `orderModes(_:by:)`, the rule shared with Android `orderModes` and web
/// `applyModeOrder`. Mirrors Android `CreateSessionViewModelReorderTest`.
@MainActor
final class CreateSessionModelReorderTests: XCTestCase {
    private let projectsJSON = """
    [{"name":"alpha","path":"/a","is_git":true,"order":0},
     {"name":"beta","path":"/b","is_git":true,"order":1},
     {"name":"gamma","path":"/g","is_git":true,"order":2}]
    """

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func idleWsClient() -> AutoScanWsClient {
        AutoScanWsClient(
            webSocketFactory: .live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            urlProvider: { nil },
            isAuthenticated: { false }
        )
    }

    private func makeTypesRepo() -> CustomAgentTypesRepository {
        CustomAgentTypesRepository(
            centralAPI: TerminalTestSupport.stubbedCentralAPI(),
            userIdentityStore: SessionHarness(userLoggedIn: true).userIdentityStore
        )
    }

    private func makeModel(typesRepo: CustomAgentTypesRepository) -> CreateSessionModel {
        makeStack(typesRepo: typesRepo).model
    }

    /// The model plus its agent bus. With `observesProjects` the model mirrors
    /// a live `ProjectsRepository` fed by `socket`.
    private func makeStack(
        typesRepo: CustomAgentTypesRepository,
        socket: FakeWebSocketTransportFactory = FakeWebSocketTransportFactory(),
        observesProjects: Bool = false
    ) -> (model: CreateSessionModel, bus: AgentEventBus) {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: .live(transportFactory: socket, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        )
        var projectsRepository: ProjectsRepository?
        if observesProjects {
            let repository = ProjectsRepository(agentAPI: agentAPI, agentEventBus: bus)
            repository.start()
            projectsRepository = repository
        }
        let model = CreateSessionModel(
            agentAPI: agentAPI,
            sessionRepository: SessionRepository(
                agentAPI: agentAPI,
                agentEventBus: bus,
                terminalSessionStore: TerminalTestSupport.store(harness)
            ),
            preferencesStore: PreferencesStore(defaults: UserDefaults(suiteName: "cw-reorder-\(UUID().uuidString)")!),
            tokenStore: harness.tokenStore,
            customAgentTypesRepository: typesRepo,
            autoScanRepository: AutoScanRepository(agentAPI: agentAPI, agentEventBus: bus, wsClient: idleWsClient()),
            projectsRepository: projectsRepository
        )
        return (model, bus)
    }

    /// A model that mirrors the agent's live project list, with the agent
    /// socket that pushes `projects.changed` already open.
    private func observedModel() async throws -> (CreateSessionModel, FakeWebSocketConnection) {
        let socket = FakeWebSocketTransportFactory()
        let (model, bus) = makeStack(typesRepo: makeTypesRepo(), socket: socket, observesProjects: true)
        await bus.start()
        let connection = try await socket.connection(1)
        connection.emit(.opened)
        return (model, connection)
    }

    private func projectsChanged(_ rows: [(name: String, order: Int)], linearKey: Bool = false) -> String {
        let payload = rows.map { row in
            #"{"name":"\#(row.name)","path":"/\#(row.name)","is_git":true,"has_linear_key":\#(linearKey),"order":\#(row.order)}"#
        }
        return #"{"type":"projects.changed","payload":{"projects":[\#(payload.joined(separator: ","))]}}"#
    }

    /// A model whose project list has loaded from the stubbed agent.
    private func loadedModel() async -> CreateSessionModel {
        StubURLProtocol.enqueue(status: 200, body: projectsJSON)
        let model = makeModel(typesRepo: makeTypesRepo())
        await model.load()
        XCTAssertEqual(model.projects.map(\.name), ["alpha", "beta", "gamma"])
        return model
    }

    private func orderJSON(_ version: Int, _ ids: [String]) -> String {
        let list = ids.map { "\"\($0)\"" }.joined(separator: ",")
        return #"{"order":[\#(list)],"version":\#(version),"updated_at":"2026-11-04T10:00:00Z"}"#
    }

    private func projectListJSON(_ names: [String]) -> String {
        let rows = names.enumerated().map { index, name in
            #"{"name":"\#(name)","path":"/\#(name)","is_git":true,"order":\#(index)}"#
        }
        return "[" + rows.joined(separator: ",") + "]"
    }

    // MARK: - orderModes

    private let defaults: [CreateSessionModel.Mode] =
        CreateSessionModel.Mode.builtIn + [.custom("aaaaaaaaaaaa"), .custom("bbbbbbbbbbbb")]

    func testOrderModesRanksNamedModesThenUnnamedInDefaultOrder() {
        let ordered = CreateSessionModel.orderModes(defaults, by: ["custom_bbbbbbbbbbbb", "shell", "codex"])

        XCTAssertEqual(
            ordered.map(\.orderId),
            [
                "custom_bbbbbbbbbbbb", "shell", "codex", "claude", "antigravity", "copilot",
                "auto_scan", "custom_aaaaaaaaaaaa",
            ],
            "a custom type created after the last reorder goes after everything the user arranged"
        )
    }

    func testOrderModesSkipsIdsThatNameNoMode() {
        let ordered = CreateSessionModel.orderModes(
            CreateSessionModel.Mode.builtIn,
            by: ["custom_deleted0000", "auto_scan", "future_mode"]
        )

        XCTAssertEqual(ordered.map(\.orderId), ["auto_scan", "claude", "codex", "antigravity", "copilot", "shell"])
    }

    func testOrderIdsMatchTheWireIdsOfEveryClient() {
        XCTAssertEqual(
            (CreateSessionModel.Mode.builtIn + [.custom("ab12cd34ef56")]).map(\.orderId),
            ["claude", "codex", "antigravity", "copilot", "auto_scan", "shell", "custom_ab12cd34ef56"]
        )
    }

    // MARK: - Mode list

    func testOrderedModesFollowsAnOrderPushedFromAnotherDevice() async {
        let typesRepo = makeTypesRepo()
        let model = makeModel(typesRepo: typesRepo)
        let observer = Task { await model.observeModeOrder() }
        defer { observer.cancel() }

        await typesRepo.applyModeOrder(SessionModeOrderDto(order: ["shell", "codex"], version: 2, updated_at: "u"))

        await waitUntil { model.orderedModes.first == .shell }
        XCTAssertEqual(model.orderedModes, [.shell, .codex, .standard, .antigravity, .copilot, .autoScan])
    }

    func testMoveModesSavesTheWholeIdListAndShowsIt() async throws {
        let typesRepo = makeTypesRepo()
        let model = makeModel(typesRepo: typesRepo)
        let observer = Task { await model.observeModeOrder() }
        defer { observer.cancel() }
        StubURLProtocol.enqueue(status: 200, body: orderJSON(1, ["shell", "claude", "codex", "antigravity", "copilot", "auto_scan"]))

        model.moveModes(fromOffsets: IndexSet(integer: 5), toOffset: 0)

        await waitUntil { typesRepo.currentModeOrder.first == "shell" && StubURLProtocol.requests.count == 1 }
        await waitUntil { model.orderedModes.first == .shell }
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["order": ["shell", "claude", "codex", "antigravity", "copilot", "auto_scan"]])
        XCTAssertEqual(model.orderedModes, [.shell, .standard, .codex, .antigravity, .copilot, .autoScan])
        XCTAssertNil(model.error)
    }

    func testMoveModesFailureRevertsAndSaysSo() async {
        let typesRepo = makeTypesRepo()
        let model = makeModel(typesRepo: typesRepo)
        let observer = Task { await model.observeModeOrder() }
        defer { observer.cancel() }
        StubURLProtocol.enqueue(status: 503, body: #"{"detail":"unavailable"}"#)

        model.moveModes(fromOffsets: IndexSet(integer: 0), toOffset: 2)

        await waitUntil { model.error != nil && model.orderedModes == CreateSessionModel.Mode.builtIn }
        XCTAssertEqual(model.error, "Reorder failed — reverted.")
        XCTAssertEqual(model.orderedModes, CreateSessionModel.Mode.builtIn)
    }

    // MARK: - Project list

    func testMoveProjectsShowsTheMoveAtOnceAndSavesTheWholeNameList() async throws {
        let model = await loadedModel()
        StubURLProtocol.enqueue(status: 200, body: projectListJSON(["beta", "gamma", "alpha"]))

        model.moveProjects(fromOffsets: IndexSet(integer: 0), toOffset: 3)
        XCTAssertEqual(model.projects.map(\.name), ["beta", "gamma", "alpha"], "the drop shows before the agent answers")

        await waitUntil { StubURLProtocol.requests.count == 2 }
        let request = StubURLProtocol.requests[1]
        XCTAssertEqual(request.httpMethod, "PATCH")
        XCTAssertEqual(request.url?.path, "/api/projects/order")
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[1])
        XCTAssertEqual(body, ["names": ["beta", "gamma", "alpha"]])
        await waitUntil { model.projects.first?.order == 0 && model.projects.first?.name == "beta" }
        XCTAssertEqual(model.projects.map(\.name), ["beta", "gamma", "alpha"])
        XCTAssertEqual(model.projects.map(\.order), [0, 1, 2], "the agent's answer replaces the local rows")
        XCTAssertNil(model.error)
    }

    func testMoveProjectsFailureRevertsToTheAgentOrderAndSaysSo() async {
        let model = await loadedModel()
        StubURLProtocol.enqueue(status: 500, body: #"{"detail":"boom"}"#)

        model.moveProjects(fromOffsets: IndexSet(integer: 2), toOffset: 0)
        XCTAssertEqual(model.projects.map(\.name), ["gamma", "alpha", "beta"])

        await waitUntil { model.error != nil }
        XCTAssertEqual(model.error, "Reorder failed — reverted.")
        XCTAssertEqual(model.projects.map(\.name), ["alpha", "beta", "gamma"])
    }

    func testTwoQuickProjectDropsSaveInOrderAndKeepTheSecondDrop() async throws {
        let model = await loadedModel()
        StubURLProtocol.enqueue(status: 200, body: projectListJSON(["beta", "alpha", "gamma"]))
        StubURLProtocol.enqueue(status: 200, body: projectListJSON(["gamma", "beta", "alpha"]))

        model.moveProjects(fromOffsets: IndexSet(integer: 0), toOffset: 2)
        model.moveProjects(fromOffsets: IndexSet(integer: 2), toOffset: 0)
        XCTAssertEqual(model.projects.map(\.name), ["gamma", "beta", "alpha"])

        await waitUntil { StubURLProtocol.requests.count == 3 && model.projects.first?.order == 0 }
        let bodies = try StubURLProtocol.bodies.dropFirst().map { try WireJSON.decoder().decode(JSONValue.self, from: $0) }
        XCTAssertEqual(bodies, [["names": ["beta", "alpha", "gamma"]], ["names": ["gamma", "beta", "alpha"]]])
        XCTAssertEqual(
            model.projects.map(\.name), ["gamma", "beta", "alpha"],
            "the first save's answer must not overwrite the newer drop"
        )
    }

    func testAFailedLaterDropRevertsToTheEarlierDropTheAgentSaved() async {
        let model = await loadedModel()
        StubURLProtocol.enqueue(status: 200, body: projectListJSON(["beta", "alpha", "gamma"]))
        StubURLProtocol.enqueue(status: 500, body: #"{"detail":"boom"}"#)

        model.moveProjects(fromOffsets: IndexSet(integer: 0), toOffset: 2)
        model.moveProjects(fromOffsets: IndexSet(integer: 2), toOffset: 0)
        XCTAssertEqual(model.projects.map(\.name), ["gamma", "beta", "alpha"])

        await waitUntil { model.error != nil }
        XCTAssertEqual(model.error, "Reorder failed — reverted.")
        XCTAssertEqual(
            model.projects.map(\.name), ["beta", "alpha", "gamma"],
            "the agent holds the first drop, so the failed second one reverts to it"
        )
    }

    // MARK: - Live project list

    func testObserveProjectsKeepsTheLoadedListThenMirrorsLivePushes() async throws {
        StubURLProtocol.enqueue(status: 200, body: projectsJSON)
        let (model, connection) = try await observedModel()
        await model.load()
        let observer = Task { await model.observeProjects() }
        defer { observer.cancel() }

        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(
            model.projects.map(\.name), ["alpha", "beta", "gamma"],
            "the replayed value is skipped, so the list load() fetched stays"
        )

        connection.emit(.text(projectsChanged([("delta", 0)])))
        await waitUntil { model.projects.map(\.name) == ["delta"] }
        connection.emit(.text(projectsChanged([])))
        await waitUntil { model.projects.isEmpty }
        XCTAssertEqual(model.projects, [], "a pushed empty list is the agent's real list")
    }

    func testObserveProjectsKeepsAProjectCreatedInline() async throws {
        let (model, connection) = try await observedModel()
        let observer = Task { await model.observeProjects() }
        defer { observer.cancel() }

        model.onProjectCreated(ProjectInfo(name: "delta", path: "/delta", is_git: true, order: 3))
        connection.emit(.text(projectsChanged([("alpha", 0), ("beta", 1)])))

        await waitUntil { model.projects.count == 3 }
        XCTAssertEqual(model.projects.map(\.name), ["alpha", "beta", "delta"])
        XCTAssertEqual(model.selection, .stored("delta"))
    }

    func testAPushDuringASaveKeepsTheDropUntilTheAgentListMatchesIt() async throws {
        let (model, connection) = try await observedModel()
        let observer = Task { await model.observeProjects() }
        defer { observer.cancel() }
        connection.emit(.text(projectsChanged([("alpha", 0), ("beta", 1), ("gamma", 2)])))
        await waitUntil { model.projects.count == 3 }
        // Holds the PATCH on the loading thread, so the save stays in flight.
        let gate = DispatchSemaphore(value: 0)
        StubURLProtocol.onRequest = { _ in gate.wait() }
        StubURLProtocol.enqueue(status: 200, body: projectListJSON(["beta", "gamma", "alpha"]))

        model.moveProjects(fromOffsets: IndexSet(integer: 0), toOffset: 3)
        await waitUntil { StubURLProtocol.requests.count == 1 }

        // An echo of an unrelated edit, still in the old order.
        connection.emit(.text(projectsChanged([("alpha", 0), ("beta", 1), ("gamma", 2)], linearKey: true)))
        await waitUntil { model.projects.allSatisfy(\.has_linear_key) }
        XCTAssertEqual(model.projects.map(\.name), ["beta", "gamma", "alpha"], "the drop stays while its save is in flight")

        // The agent's echo of the drop itself.
        connection.emit(.text(projectsChanged([("beta", 0), ("gamma", 1), ("alpha", 2)])))
        await waitUntil { model.projects.first(where: { $0.name == "alpha" })?.order == 2 }

        // Another device moves alpha back before the PATCH answer arrives.
        connection.emit(.text(projectsChanged([("alpha", 0), ("beta", 1), ("gamma", 2)])))
        await waitUntil { model.projects.first?.name == "alpha" }
        XCTAssertEqual(
            model.projects.map(\.name), ["alpha", "beta", "gamma"],
            "once the agent's list matched the drop, later pushes show as they are"
        )

        StubURLProtocol.onRequest = nil
        gate.signal()
    }

    // MARK: - Replayed project list

    /// Like `observedModel()`, but the shared `ProjectsRepository` already
    /// holds `staleRows` before the model exists, as it does after a sign-out
    /// or an agent switch (the app-scoped repository is not purged).
    private func modelOverAStaleRepository(
        _ staleRows: [(name: String, order: Int)]
    ) async throws -> (CreateSessionModel, FakeWebSocketConnection) {
        let socket = FakeWebSocketTransportFactory()
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: .live(transportFactory: socket, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        )
        let repository = ProjectsRepository(agentAPI: agentAPI, agentEventBus: bus)
        repository.start()
        await bus.start()
        let connection = try await socket.connection(1)
        connection.emit(.opened)
        connection.emit(.text(projectsChanged(staleRows)))
        await waitUntil { repository.currentProjects.count == staleRows.count }
        XCTAssertEqual(repository.currentProjects.map(\.name), staleRows.map(\.name))

        let model = CreateSessionModel(
            agentAPI: agentAPI,
            sessionRepository: SessionRepository(
                agentAPI: agentAPI,
                agentEventBus: bus,
                terminalSessionStore: TerminalTestSupport.store(harness)
            ),
            preferencesStore: PreferencesStore(defaults: UserDefaults(suiteName: "cw-reorder-\(UUID().uuidString)")!),
            tokenStore: harness.tokenStore,
            customAgentTypesRepository: makeTypesRepo(),
            autoScanRepository: AutoScanRepository(agentAPI: agentAPI, agentEventBus: bus, wsClient: idleWsClient()),
            projectsRepository: repository
        )
        return (model, connection)
    }

    func testObserveProjectsDoesNotShowANonEmptyListTheRepositoryAlreadyHeld() async throws {
        let (model, connection) = try await modelOverAStaleRepository([("stale1", 0), ("stale2", 1)])
        let observer = Task { await model.observeProjects() }
        defer { observer.cancel() }

        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(model.projects, [], "the replayed list can belong to another account or agent")

        connection.emit(.text(projectsChanged([("alpha", 1), ("beta", 0)])))
        await waitUntil { model.projects.count == 2 }
        XCTAssertEqual(model.projects.map(\.name), ["beta", "alpha"], "a push after the replay is shown, sorted by order")
    }

    func testObserveProjectsKeepsTheLoadedListOverAStaleReplay() async throws {
        let (model, connection) = try await modelOverAStaleRepository([("stale1", 0), ("stale2", 1)])
        StubURLProtocol.enqueue(status: 200, body: projectsJSON)
        await model.load()
        let observer = Task { await model.observeProjects() }
        defer { observer.cancel() }

        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(
            model.projects.map(\.name), ["alpha", "beta", "gamma"],
            "the stale replay must not overwrite the list load() fetched"
        )

        connection.emit(.text(projectsChanged([("delta", 0)])))
        await waitUntil { model.projects.map(\.name) == ["delta"] }
        XCTAssertEqual(model.projects.map(\.name), ["delta"])
    }

    func testLoadFailureDoesNotFallBackToTheListTheRepositoryAlreadyHeld() async throws {
        let (model, _) = try await modelOverAStaleRepository([("stale1", 0), ("stale2", 1)])
        StubURLProtocol.enqueue(status: 500, body: #"{"detail":"boom"}"#)

        await model.load()

        XCTAssertEqual(StubURLProtocol.requests.map(\.url?.path), ["/api/projects"])
        XCTAssertEqual(model.projects, [], "the repository's list can belong to another account or agent")
    }
}
