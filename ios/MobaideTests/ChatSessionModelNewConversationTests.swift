import XCTest
@testable import Mobaide

/// New conversation in `ChatSessionModel`: only an agent that offers it gets
/// the command, never while it starts (the user is told), and a double tap
/// sends one command without a message.
@MainActor
final class ChatSessionModelNewConversationTests: XCTestCase {

    private struct Live {
        let model: ChatSessionModel
        let connection: FakeWebSocketConnection
    }

    /// A model on a live socket whose snapshot state is `stateJSON` (without braces).
    private func liveModel(stateJSON: String) async throws -> Live {
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
        emit(connection, #"{"t":"snapshot","epoch":"e1","seq":1,"truncated":false,"state":{"#
            + stateJSON + #"},"items":[]}"#)
        await waitUntil { model.isLive && model.phase == .ready }
        return Live(model: model, connection: connection)
    }

    private func emit(_ connection: FakeWebSocketConnection, _ frame: String) {
        connection.emit(.binary(Data([ChatFrameType.agent]) + Data(frame.utf8)))
    }

    /// The ids of the `new_conversation` commands sent so far.
    private func newConversationIds(_ connection: FakeWebSocketConnection) throws -> [String] {
        try connection.sentBinaries
            .filter { $0.first == ChatFrameType.command }
            .map { try WireJSON.decoder().decode(JSONValue.self, from: Data($0.dropFirst())) }
            .compactMap { value -> String? in
                guard case .object(let fields) = value, fields["op"] == .string("new_conversation"),
                      case .string(let id) = fields["id"] else { return nil }
                return id
            }
    }

    func testADoubleTapSendsOneCommandAndAFailureShowsTheAgentsErrorAndAllowsATryAgain() async throws {
        let live = try await liveModel(stateJSON: #""status":"ready","new_conversation":true"#)
        defer { live.model.disconnect() }

        live.model.newConversation()
        live.model.newConversation()
        await waitUntil { (try? self.newConversationIds(live.connection).count) == 1 }

        XCTAssertTrue(live.model.startingNew)
        XCTAssertFalse(live.model.canStartNew)
        XCTAssertNil(live.model.message)

        let id = try XCTUnwrap(newConversationIds(live.connection).first)
        emit(live.connection, #"{"t":"result","id":"\#(id)","ok":false,"error":"The agent is already starting"}"#)
        await waitUntil { !live.model.startingNew }

        XCTAssertEqual(live.model.message?.text, "The agent is already starting")
        live.model.newConversation()
        await waitUntil { (try? self.newConversationIds(live.connection).count) == 2 }
        XCTAssertEqual(try newConversationIds(live.connection).count, 2)
    }

    func testAnAgentWithoutNewConversationOrOneThatIsStartingGetsNoCommandAndTheUserIsTold() async throws {
        let live = try await liveModel(stateJSON: #""status":"ready""#)
        defer { live.model.disconnect() }
        let refused = "Could not start a new conversation now. Try again."

        XCTAssertFalse(live.model.offersNewConversation)
        live.model.newConversation()
        XCTAssertFalse(live.model.startingNew)
        let first = try XCTUnwrap(live.model.message)
        XCTAssertEqual(first.text, refused)
        XCTAssertEqual(first.tone, .danger)

        emit(live.connection, #"{"t":"events","epoch":"e1","seq":2,"events":[{"seq":2,"type":"state","#
            + #""patch":{"status":"starting","new_conversation":true}}]}"#)
        await waitUntil { live.model.phase == .starting }

        XCTAssertTrue(live.model.offersNewConversation)
        live.model.newConversation()
        XCTAssertFalse(live.model.startingNew)
        XCTAssertEqual(try newConversationIds(live.connection), [])
        let second = try XCTUnwrap(live.model.message)
        XCTAssertNotEqual(second.id, first.id)
        XCTAssertEqual(second.text, refused)
    }
}
