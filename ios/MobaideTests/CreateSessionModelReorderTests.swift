import XCTest
@testable import Mobaide

/// Reordering in the create-session wizard: the workspace list saves the
/// agent's own workspace order (`PATCH /api/projects/order`), the mode list
/// saves the account's mode order through `CustomAgentTypesRepository`. Both
/// show a move at once; a failed save reverts and says so. Also pins
/// `orderModes(_:by:)`, the rule shared with Android `orderModes` and web
/// `applyModeOrder`. Mirrors Android `CreateSessionViewModelReorderTest`.
@MainActor
final class CreateSessionModelReorderTests: XCTestCase {
    private let workspacesJSON = """
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

    /// The model plus its agent bus. With `observesWorkspaces` the model mirrors
    /// a live `WorkspacesRepository` fed by `socket`.
    private func makeStack(
        typesRepo: CustomAgentTypesRepository,
        socket: FakeWebSocketTransportFactory = FakeWebSocketTransportFactory(),
        observesWorkspaces: Bool = false
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
        var workspacesRepository: WorkspacesRepository?
        if observesWorkspaces {
            let repository = WorkspacesRepository(agentAPI: agentAPI, agentEventBus: bus)
            repository.start()
            workspacesRepository = repository
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
            workspacesRepository: workspacesRepository
        )
        return (model, bus)
    }

    /// A model that mirrors the agent's live workspace list, with the agent
    /// socket that pushes `projects.changed` already open.
    private func observedModel() async throws -> (CreateSessionModel, FakeWebSocketConnection) {
        let socket = FakeWebSocketTransportFactory()
        let (model, bus) = makeStack(typesRepo: makeTypesRepo(), socket: socket, observesWorkspaces: true)
        await bus.start()
        let connection = try await socket.connection(1)
        connection.emit(.opened)
        return (model, connection)
    }

    private func workspacesChanged(_ rows: [(name: String, order: Int)], linearKey: Bool = false) -> String {
        let payload = rows.map { row in
            #"{"name":"\#(row.name)","path":"/\#(row.name)","is_git":true,"has_linear_key":\#(linearKey),"order":\#(row.order)}"#
        }
        return #"{"type":"projects.changed","payload":{"projects":[\#(payload.joined(separator: ","))]}}"#
    }

    /// A model whose workspace list has loaded from the stubbed agent.
    private func loadedModel() async -> CreateSessionModel {
        StubURLProtocol.enqueue(status: 200, body: workspacesJSON)
        let model = makeModel(typesRepo: makeTypesRepo())
        await model.load()
        XCTAssertEqual(model.workspaces.map(\.name), ["alpha", "beta", "gamma"])
        return model
    }

    private func orderJSON(_ version: Int, _ ids: [String]) -> String {
        let list = ids.map { "\"\($0)\"" }.joined(separator: ",")
        return #"{"order":[\#(list)],"version":\#(version),"updated_at":"2026-11-04T10:00:00Z"}"#
    }

    private func workspaceListJSON(_ names: [String]) -> String {
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
                "custom_bbbbbbbbbbbb", "shell", "codex", "claude", "antigravity", "opencode", "copilot", "cursor", "pi",
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

        XCTAssertEqual(
            ordered.map(\.orderId),
            ["auto_scan", "claude", "codex", "antigravity", "opencode", "copilot", "cursor", "pi", "shell"]
        )
    }

    func testOrderIdsMatchTheWireIdsOfEveryClient() {
        XCTAssertEqual(
            (CreateSessionModel.Mode.builtIn + [.custom("ab12cd34ef56")]).map(\.orderId),
            ["claude", "codex", "antigravity", "opencode", "copilot", "cursor", "pi", "auto_scan", "shell", "custom_ab12cd34ef56"]
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
        XCTAssertEqual(model.orderedModes, [.shell, .codex, .standard, .antigravity, .opencode, .copilot, .cursor, .pi, .autoScan])
    }

    func testMoveModesSavesTheWholeIdListAndShowsIt() async throws {
        let typesRepo = makeTypesRepo()
        let model = makeModel(typesRepo: typesRepo)
        let observer = Task { await model.observeModeOrder() }
        defer { observer.cancel() }
        StubURLProtocol.enqueue(
            status: 200,
            body: orderJSON(1, ["shell", "claude", "codex", "antigravity", "opencode", "copilot", "cursor", "pi", "auto_scan"])
        )

        model.moveModes(fromOffsets: IndexSet(integer: 8), toOffset: 0)

        await waitUntil { typesRepo.currentModeOrder.first == "shell" && StubURLProtocol.requests.count == 1 }
        await waitUntil { model.orderedModes.first == .shell }
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["order": ["shell", "claude", "codex", "antigravity", "opencode", "copilot", "cursor", "pi", "auto_scan"]])
        XCTAssertEqual(model.orderedModes, [.shell, .standard, .codex, .antigravity, .opencode, .copilot, .cursor, .pi, .autoScan])
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

    // MARK: - Workspace list

    func testMoveWorkspacesShowsTheMoveAtOnceAndSavesTheWholeNameList() async throws {
        let model = await loadedModel()
        StubURLProtocol.enqueue(status: 200, body: workspaceListJSON(["beta", "gamma", "alpha"]))

        model.moveWorkspaces(fromOffsets: IndexSet(integer: 0), toOffset: 3)
        XCTAssertEqual(model.workspaces.map(\.name), ["beta", "gamma", "alpha"], "the drop shows before the agent answers")

        await waitUntil { StubURLProtocol.requests.count == 2 }
        let request = StubURLProtocol.requests[1]
        XCTAssertEqual(request.httpMethod, "PATCH")
        XCTAssertEqual(request.url?.path, "/api/projects/order")
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[1])
        XCTAssertEqual(body, ["names": ["beta", "gamma", "alpha"]])
        await waitUntil { model.workspaces.first?.order == 0 && model.workspaces.first?.name == "beta" }
        XCTAssertEqual(model.workspaces.map(\.name), ["beta", "gamma", "alpha"])
        XCTAssertEqual(model.workspaces.map(\.order), [0, 1, 2], "the agent's answer replaces the local rows")
        XCTAssertNil(model.error)
    }

    func testMoveWorkspacesFailureRevertsToTheAgentOrderAndSaysSo() async {
        let model = await loadedModel()
        StubURLProtocol.enqueue(status: 500, body: #"{"detail":"boom"}"#)

        model.moveWorkspaces(fromOffsets: IndexSet(integer: 2), toOffset: 0)
        XCTAssertEqual(model.workspaces.map(\.name), ["gamma", "alpha", "beta"])

        await waitUntil { model.error != nil }
        XCTAssertEqual(model.error, "Reorder failed — reverted.")
        XCTAssertEqual(model.workspaces.map(\.name), ["alpha", "beta", "gamma"])
    }

    func testTwoQuickWorkspaceDropsSaveInOrderAndKeepTheSecondDrop() async throws {
        let model = await loadedModel()
        StubURLProtocol.enqueue(status: 200, body: workspaceListJSON(["beta", "alpha", "gamma"]))
        StubURLProtocol.enqueue(status: 200, body: workspaceListJSON(["gamma", "beta", "alpha"]))

        model.moveWorkspaces(fromOffsets: IndexSet(integer: 0), toOffset: 2)
        model.moveWorkspaces(fromOffsets: IndexSet(integer: 2), toOffset: 0)
        XCTAssertEqual(model.workspaces.map(\.name), ["gamma", "beta", "alpha"])

        await waitUntil { StubURLProtocol.requests.count == 3 && model.workspaces.first?.order == 0 }
        let bodies = try StubURLProtocol.bodies.dropFirst().map { try WireJSON.decoder().decode(JSONValue.self, from: $0) }
        XCTAssertEqual(bodies, [["names": ["beta", "alpha", "gamma"]], ["names": ["gamma", "beta", "alpha"]]])
        XCTAssertEqual(
            model.workspaces.map(\.name), ["gamma", "beta", "alpha"],
            "the first save's answer must not overwrite the newer drop"
        )
    }

    func testAFailedLaterDropRevertsToTheEarlierDropTheAgentSaved() async {
        let model = await loadedModel()
        StubURLProtocol.enqueue(status: 200, body: workspaceListJSON(["beta", "alpha", "gamma"]))
        StubURLProtocol.enqueue(status: 500, body: #"{"detail":"boom"}"#)

        model.moveWorkspaces(fromOffsets: IndexSet(integer: 0), toOffset: 2)
        model.moveWorkspaces(fromOffsets: IndexSet(integer: 2), toOffset: 0)
        XCTAssertEqual(model.workspaces.map(\.name), ["gamma", "beta", "alpha"])

        await waitUntil { model.error != nil }
        XCTAssertEqual(model.error, "Reorder failed — reverted.")
        XCTAssertEqual(
            model.workspaces.map(\.name), ["beta", "alpha", "gamma"],
            "the agent holds the first drop, so the failed second one reverts to it"
        )
    }

    // MARK: - Live workspace list

    func testObserveWorkspacesKeepsTheLoadedListThenMirrorsLivePushes() async throws {
        StubURLProtocol.enqueue(status: 200, body: workspacesJSON)
        let (model, connection) = try await observedModel()
        await model.load()
        let observer = Task { await model.observeWorkspaces() }
        defer { observer.cancel() }

        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(
            model.workspaces.map(\.name), ["alpha", "beta", "gamma"],
            "the replayed value is skipped, so the list load() fetched stays"
        )

        connection.emit(.text(workspacesChanged([("delta", 0)])))
        await waitUntil { model.workspaces.map(\.name) == ["delta"] }
        connection.emit(.text(workspacesChanged([])))
        await waitUntil { model.workspaces.isEmpty }
        XCTAssertEqual(model.workspaces, [], "a pushed empty list is the agent's real list")
    }

    func testObserveWorkspacesKeepsAWorkspaceCreatedInline() async throws {
        let (model, connection) = try await observedModel()
        let observer = Task { await model.observeWorkspaces() }
        defer { observer.cancel() }

        model.onWorkspaceCreated(WorkspaceInfo(name: "delta", path: "/delta", is_git: true, order: 3))
        connection.emit(.text(workspacesChanged([("alpha", 0), ("beta", 1)])))

        await waitUntil { model.workspaces.count == 3 }
        XCTAssertEqual(model.workspaces.map(\.name), ["alpha", "beta", "delta"])
        XCTAssertEqual(model.selection, .stored("delta"))
    }

    func testAPushDuringASaveKeepsTheDropUntilTheAgentListMatchesIt() async throws {
        let (model, connection) = try await observedModel()
        let observer = Task { await model.observeWorkspaces() }
        defer { observer.cancel() }
        connection.emit(.text(workspacesChanged([("alpha", 0), ("beta", 1), ("gamma", 2)])))
        await waitUntil { model.workspaces.count == 3 }
        // Holds the PATCH on the loading thread, so the save stays in flight.
        let gate = DispatchSemaphore(value: 0)
        StubURLProtocol.onRequest = { _ in gate.wait() }
        StubURLProtocol.enqueue(status: 200, body: workspaceListJSON(["beta", "gamma", "alpha"]))

        model.moveWorkspaces(fromOffsets: IndexSet(integer: 0), toOffset: 3)
        await waitUntil { StubURLProtocol.requests.count == 1 }

        // An echo of an unrelated edit, still in the old order.
        connection.emit(.text(workspacesChanged([("alpha", 0), ("beta", 1), ("gamma", 2)], linearKey: true)))
        await waitUntil { model.workspaces.allSatisfy(\.has_linear_key) }
        XCTAssertEqual(model.workspaces.map(\.name), ["beta", "gamma", "alpha"], "the drop stays while its save is in flight")

        // The agent's echo of the drop itself.
        connection.emit(.text(workspacesChanged([("beta", 0), ("gamma", 1), ("alpha", 2)])))
        await waitUntil { model.workspaces.first(where: { $0.name == "alpha" })?.order == 2 }

        // Another device moves alpha back before the PATCH answer arrives.
        connection.emit(.text(workspacesChanged([("alpha", 0), ("beta", 1), ("gamma", 2)])))
        await waitUntil { model.workspaces.first?.name == "alpha" }
        XCTAssertEqual(
            model.workspaces.map(\.name), ["alpha", "beta", "gamma"],
            "once the agent's list matched the drop, later pushes show as they are"
        )

        StubURLProtocol.onRequest = nil
        gate.signal()
    }

    // MARK: - Replayed workspace list

    /// Like `observedModel()`, but the shared `WorkspacesRepository` already
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
        let repository = WorkspacesRepository(agentAPI: agentAPI, agentEventBus: bus)
        repository.start()
        await bus.start()
        let connection = try await socket.connection(1)
        connection.emit(.opened)
        connection.emit(.text(workspacesChanged(staleRows)))
        await waitUntil { repository.currentWorkspaces.count == staleRows.count }
        XCTAssertEqual(repository.currentWorkspaces.map(\.name), staleRows.map(\.name))

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
            workspacesRepository: repository
        )
        return (model, connection)
    }

    func testObserveWorkspacesDoesNotShowANonEmptyListTheRepositoryAlreadyHeld() async throws {
        let (model, connection) = try await modelOverAStaleRepository([("stale1", 0), ("stale2", 1)])
        let observer = Task { await model.observeWorkspaces() }
        defer { observer.cancel() }

        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(model.workspaces, [], "the replayed list can belong to another account or agent")

        connection.emit(.text(workspacesChanged([("alpha", 1), ("beta", 0)])))
        await waitUntil { model.workspaces.count == 2 }
        XCTAssertEqual(model.workspaces.map(\.name), ["beta", "alpha"], "a push after the replay is shown, sorted by order")
    }

    func testObserveWorkspacesKeepsTheLoadedListOverAStaleReplay() async throws {
        let (model, connection) = try await modelOverAStaleRepository([("stale1", 0), ("stale2", 1)])
        StubURLProtocol.enqueue(status: 200, body: workspacesJSON)
        await model.load()
        let observer = Task { await model.observeWorkspaces() }
        defer { observer.cancel() }

        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(
            model.workspaces.map(\.name), ["alpha", "beta", "gamma"],
            "the stale replay must not overwrite the list load() fetched"
        )

        connection.emit(.text(workspacesChanged([("delta", 0)])))
        await waitUntil { model.workspaces.map(\.name) == ["delta"] }
        XCTAssertEqual(model.workspaces.map(\.name), ["delta"])
    }

    func testLoadFailureDoesNotFallBackToTheListTheRepositoryAlreadyHeld() async throws {
        let (model, _) = try await modelOverAStaleRepository([("stale1", 0), ("stale2", 1)])
        StubURLProtocol.enqueue(status: 500, body: #"{"detail":"boom"}"#)

        await model.load()

        XCTAssertEqual(StubURLProtocol.requests.map(\.url?.path), ["/api/projects"])
        XCTAssertEqual(model.workspaces, [], "the repository's list can belong to another account or agent")
    }
}
