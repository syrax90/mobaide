import XCTest
@testable import Mobaide

/// `@` file mentions in `ChatSessionModel`: a picked file replaces the token
/// at the cursor, goes out in the prompt's `mentions`, and comes back with the
/// draft when the agent refuses the prompt.
@MainActor
final class ChatSessionModelMentionsTests: XCTestCase {

    private struct Live {
        let model: ChatSessionModel
        let connection: FakeWebSocketConnection
    }

    /// A model on a live socket whose snapshot says the agent takes mentions.
    private func liveModel() async throws -> Live {
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
        emit(connection, #"{"t":"snapshot","epoch":"e1","seq":1,"truncated":false,"state":{"status":"ready","prompt_caps":{"image":false,"mentions":true}},"items":[]}"#)
        await waitUntil { model.canAct && model.state.promptMentions }
        XCTAssertTrue(model.canAct && model.state.promptMentions, "the model never went live with mentions")
        return Live(model: model, connection: connection)
    }

    private func emit(_ connection: FakeWebSocketConnection, _ frame: String) {
        connection.emit(.binary(Data([ChatFrameType.agent]) + Data(frame.utf8)))
    }

    /// The `prompt` commands sent so far.
    private func prompts(_ connection: FakeWebSocketConnection) throws -> [[String: JSONValue]] {
        try connection.sentBinaries
            .filter { $0.first == ChatFrameType.command }
            .map { try WireJSON.decoder().decode(JSONValue.self, from: Data($0.dropFirst())) }
            .compactMap { value -> [String: JSONValue]? in
                guard case .object(let fields) = value, fields["op"] == .string("prompt") else { return nil }
                return fields
            }
    }

    func testAPickedFileGoesOutAsAMentionAndARefusedPromptBringsItBack() async throws {
        let live = try await liveModel()
        defer { live.model.disconnect() }
        live.model.composerText = "read @sr"

        live.model.pickMention(SessionFileEntry(path: "src/app.py", dir: false))
        XCTAssertEqual(live.model.composerText, "read @src/app.py ")
        live.model.send()
        await waitUntil { (try? self.prompts(live.connection).count) == 1 }

        let first = try XCTUnwrap(try prompts(live.connection).first)
        XCTAssertEqual(first["text"], .string("read @src/app.py"))
        XCTAssertEqual(first["mentions"], .array([.string("src/app.py")]))
        guard case .string(let id) = first["id"] else { return XCTFail("the prompt has no id") }

        emit(live.connection, #"{"t":"result","id":"\#(id)","ok":false,"error":"Too many queued messages"}"#)
        await waitUntil { !live.model.composerText.isEmpty }
        XCTAssertEqual(live.model.composerText, "read @src/app.py")
        live.model.send()
        await waitUntil { (try? self.prompts(live.connection).count) == 2 }

        let again = try XCTUnwrap(try prompts(live.connection).last)
        XCTAssertEqual(again["mentions"], .array([.string("src/app.py")]))
    }

}
