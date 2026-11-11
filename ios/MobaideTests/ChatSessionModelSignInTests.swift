import XCTest
@testable import Mobaide

/// The sign-in card's commands in `ChatSessionModel`: start, submit (trimmed,
/// never blank, never while the PC checks the last code) and cancel, one at a
/// time per card, with a refusal shown on the card.
@MainActor
final class ChatSessionModelSignInTests: XCTestCase {

    private struct Live {
        let model: ChatSessionModel
        let connection: FakeWebSocketConnection
        let request: ChatPendingRequest.SignIn
    }

    /// A model on a live socket whose only request is a sign-in with these extra fields.
    private func liveModel(signInFields: String = "") async throws -> Live {
        let factory = FakeWebSocketTransportFactory()
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let tracker = ForegroundSessionTracker()
        let model = ChatSessionModel(
            sessionId: "chat-1",
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore,
            foregroundSessionTracker: tracker,
            reviewPrompt: TerminalTestSupport.reviewPrompt(
                preferencesStore: TerminalTestSupport.isolatedPreferencesStore(),
                foregroundSessionTracker: tracker
            )
        )
        model.markVisible()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        let request = #"{"id":"sign-in","kind":"sign_in","tool_call_id":null,"title":"Sign in to Claude Code","#
            + #""text":"","methods":[{"id":"claude_ai","name":"Claude subscription","description":""}]"#
            + signInFields + "}"
        let snapshot = #"{"t":"snapshot","epoch":"e1","seq":1,"truncated":false,"state":{"status":"ready","requests":["#
            + request + #"]},"items":[]}"#
        emit(connection, snapshot)
        await waitUntil { model.canAct && !model.state.requests.isEmpty }
        guard case .signIn(let signIn) = model.state.requests.first else {
            XCTFail("the snapshot gave no sign-in request")
            throw CancellationError()
        }
        return Live(model: model, connection: connection, request: signIn)
    }

    private func waitingForCode(busy: Bool = false) async throws -> Live {
        try await liveModel(signInFields: #","stage":"waiting","url":"https://example.com/sign-in","#
            + #""input":{"label":"Code from the sign-in page","secret":false},"busy":\#(busy)"#)
    }

    private func emit(_ connection: FakeWebSocketConnection, _ frame: String) {
        connection.emit(.binary(Data([ChatFrameType.agent]) + Data(frame.utf8)))
    }

    /// The `sign_in` commands sent so far, without their ids.
    private func signInCommands(_ connection: FakeWebSocketConnection) throws -> [JSONValue] {
        try connection.sentBinaries
            .filter { $0.first == ChatFrameType.command }
            .map { try WireJSON.decoder().decode(JSONValue.self, from: Data($0.dropFirst())) }
            .compactMap { value -> JSONValue? in
                guard case .object(var fields) = value, fields["op"] == .string("sign_in") else { return nil }
                fields["id"] = nil
                return .object(fields)
            }
    }

    private func lastCommandId(_ connection: FakeWebSocketConnection) throws -> String {
        let data = try XCTUnwrap(connection.sentBinaries.last { $0.first == ChatFrameType.command })
        let value = try WireJSON.decoder().decode(JSONValue.self, from: Data(data.dropFirst()))
        guard case .object(let fields) = value, case .string(let id) = fields["id"] else {
            XCTFail("the command has no id")
            return ""
        }
        return id
    }

    func testStartSendsTheMethodAndTakesNoSecondCommandUntilTheResult() async throws {
        let live = try await liveModel()
        defer { live.model.disconnect() }
        let method = try XCTUnwrap(live.request.methods.first)

        live.model.startSignIn(live.request, method: method)
        live.model.startSignIn(live.request, method: method)
        live.model.cancelSignIn(live.request)
        await waitUntil { (try? self.signInCommands(live.connection).count) == 1 }

        XCTAssertEqual(try signInCommands(live.connection), [
            .object(["op": .string("sign_in"), "action": .string("start"), "method": .string("claude_ai")]),
        ])
        XCTAssertEqual(live.model.answering["sign-in"], ChatSessionModel.signInStartKey("claude_ai"))

        let id = try lastCommandId(live.connection)
        emit(live.connection, #"{"t":"result","id":"\#(id)","ok":true}"#)
        await waitUntil { live.model.answering["sign-in"] == nil }
        live.model.cancelSignIn(live.request)
        await waitUntil { (try? self.signInCommands(live.connection).count) == 2 }

        XCTAssertNil(live.model.formErrors["sign-in"])
        XCTAssertEqual(try signInCommands(live.connection).last, .object([
            "op": .string("sign_in"), "action": .string("cancel"),
        ]))
    }

    func testSubmitSendsTheTrimmedCode() async throws {
        let live = try await waitingForCode()
        defer { live.model.disconnect() }

        live.model.submitSignIn(live.request, value: "  ab12-cd34 \n")
        await waitUntil { (try? self.signInCommands(live.connection).count) == 1 }

        XCTAssertEqual(try signInCommands(live.connection), [
            .object(["op": .string("sign_in"), "action": .string("submit"), "value": .string("ab12-cd34")]),
        ])
        XCTAssertEqual(live.model.answering["sign-in"], ChatSessionModel.signInSubmitKey)
    }

    func testABlankCodeSendsNothing() async throws {
        let live = try await waitingForCode()
        defer { live.model.disconnect() }

        live.model.submitSignIn(live.request, value: "  \n ")

        XCTAssertNil(live.model.answering["sign-in"])
        XCTAssertTrue(try signInCommands(live.connection).isEmpty)
    }

    func testNoCodeGoesOutWhileThePCChecksTheLastOne() async throws {
        let live = try await waitingForCode(busy: true)
        defer { live.model.disconnect() }

        live.model.submitSignIn(live.request, value: "ab12-cd34")

        XCTAssertNil(live.model.answering["sign-in"])
        XCTAssertTrue(try signInCommands(live.connection).isEmpty)
    }

    func testARefusedCommandShowsTheAgentsErrorOnTheCard() async throws {
        let live = try await waitingForCode()
        defer { live.model.disconnect() }
        live.model.submitSignIn(live.request, value: "ab12")
        await waitUntil { (try? self.signInCommands(live.connection).count) == 1 }

        let id = try lastCommandId(live.connection)
        emit(live.connection, #"{"t":"result","id":"\#(id)","ok":false,"error":"The sign-in is not running"}"#)
        await waitUntil { live.model.answering["sign-in"] == nil }

        XCTAssertEqual(live.model.formErrors["sign-in"], "The sign-in is not running")
    }

    func testOnlyAnHttpsSignInPageIsKept() async throws {
        let live = try await liveModel(signInFields: #","stage":"waiting","url":"http://example.com/sign-in""#)
        defer { live.model.disconnect() }

        XCTAssertTrue(live.request.waiting)
        XCTAssertNil(live.request.url)
    }
}
