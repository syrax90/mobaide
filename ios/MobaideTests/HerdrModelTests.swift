import XCTest
@testable import Mobaide

/// Where the Herdr dashboard puts the reason of a failed action. The create
/// sheet covers a toast, so its failure belongs in the sheet; a split has no
/// sheet, so its failure is a toast.
@MainActor
final class HerdrModelTests: XCTestCase {
    private static let missingFolder = "This folder does not exist: /missing"
    private static let refusal = #"{"detail":{"code":"invalid_folder","message":"This folder does not exist: /missing"}}"#
    private static let server = HerdrServer(server_id: "tui:default")

    private var suiteName: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        suiteName = "herdr-model-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private func makeModel() -> HerdrModel {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: client
        )
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(
                transportFactory: FakeWebSocketTransportFactory(),
                sleeper: RecordingSleeper()
            ),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        return HerdrModel(
            repository: HerdrRepository(agentAPI: agentAPI, agentEventBus: bus, connectedAgentId: { "agent-1" }),
            seenStore: HerdrSeenStore(defaults: defaults),
            projectsRepository: ProjectsRepository(agentAPI: agentAPI, agentEventBus: bus)
        )
    }

    private func newWorkspace() -> HerdrCreateRequest {
        HerdrCreateRequest(
            server_id: "tui:default", placement: HerdrCreateRequest.placementWorkspace, cwd: "/missing"
        )
    }

    func testAFailedCreateShowsItsReasonInTheSheetAndRaisesNoToast() async {
        StubURLProtocol.enqueue(status: 422, body: Self.refusal)
        let model = makeModel()
        model.dialog = .create(server: Self.server, workspaceId: nil)

        await model.create(newWorkspace())

        XCTAssertEqual(model.actionError, Self.missingFolder)
        XCTAssertNil(model.message, "a toast would be under the sheet")
        XCTAssertNotNil(model.dialog, "the sheet stays open with what the user typed")
        XCTAssertNil(model.openRequest)
        XCTAssertFalse(model.busy)
    }

    func testAFailedSplitIsAToastBecauseItHasNoSheet() async {
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":{"code":"herdr_refused","message":"The pane is too small."}}"#)
        let model = makeModel()

        await model.create(
            HerdrCreateRequest(
                server_id: "tui:default", placement: HerdrCreateRequest.placementSplitRight, pane_id: "w1:p1"
            )
        )

        XCTAssertEqual(model.message?.text, "The pane is too small.")
        // The next sheet must not open with the reason of a split.
        XCTAssertNil(model.actionError)
    }

    func testTheReasonGoesAwayWhenTheSheetClosesAndWhenAnotherOpens() async {
        let model = makeModel()
        func failCreate() async {
            StubURLProtocol.enqueue(status: 422, body: Self.refusal)
            await model.create(newWorkspace())
        }

        model.dialog = .create(server: Self.server, workspaceId: nil)
        await failCreate()
        XCTAssertEqual(model.actionError, Self.missingFolder)
        model.dialog = nil
        XCTAssertNil(model.actionError, "the sheet closed")

        model.dialog = .create(server: Self.server, workspaceId: nil)
        await failCreate()
        XCTAssertEqual(model.actionError, Self.missingFolder)
        model.dialog = .create(server: Self.server, workspaceId: "w1")
        XCTAssertNil(model.actionError, "the reason of one sheet is not shown in the next")

        // A try that works closes the sheet and leaves no reason behind.
        await failCreate()
        StubURLProtocol.enqueue(status: 200, body: #"{"pane_id":"w1:p9"}"#)
        StubURLProtocol.enqueue(status: 200, body: #"{"session_id":"herdr-0123"}"#)
        await model.create(newWorkspace())

        XCTAssertNil(model.actionError)
        XCTAssertNil(model.dialog)
        XCTAssertEqual(
            model.openRequest,
            HerdrOpenRequest(sessionId: "herdr-0123", serverId: "tui:default", paneId: "w1:p9")
        )
    }

    func testTheRowBeingOpenedIsNamedWhileItsCallRunsAndNoLongerAfterIt() async {
        // The row shows progress from the tap to the agent's answer. A key
        // that stays after a refusal leaves every row dead: no pane opens.
        let opened = ForegroundSessionTracker.herdrPaneKey(serverId: "tui:default", paneId: "w1:p1")
        let answers = [
            (200, #"{"session_id":"herdr-0123"}"#),
            (404, #"{"detail":{"code":"pane_not_found","message":"This pane is closed."}}"#),
        ]
        for (status, answer) in answers {
            StubURLProtocol.reset()
            StubURLProtocol.enqueue(status: status, body: answer)
            let model = makeModel()
            // Holds the first call on the loading thread, so the open stays in flight.
            let gate = DispatchSemaphore(value: 0)
            StubURLProtocol.onRequest = { _ in
                StubURLProtocol.onRequest = nil
                gate.wait()
            }

            let open = Task { await model.openPane(serverId: "tui:default", paneId: "w1:p1") }
            await waitUntil { StubURLProtocol.requests.count == 1 }
            XCTAssertEqual(model.openingKey, opened, "status \(status)")

            // A tap on another row while the first open runs starts no call.
            await model.openPane(serverId: "tui:default", paneId: "w1:p2")
            XCTAssertEqual(model.openingKey, opened, "status \(status)")
            XCTAssertEqual(StubURLProtocol.requests.count, 1, "status \(status)")

            gate.signal()
            await open.value

            XCTAssertNil(model.openingKey, "status \(status)")
            if status == 200 {
                XCTAssertEqual(
                    model.openRequest,
                    HerdrOpenRequest(sessionId: "herdr-0123", serverId: "tui:default", paneId: "w1:p1")
                )
            } else {
                XCTAssertNil(model.openRequest)
                XCTAssertEqual(model.message?.text, "This pane is closed.")
            }
        }
    }
}
