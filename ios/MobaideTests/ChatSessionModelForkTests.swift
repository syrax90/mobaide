import XCTest
@testable import Mobaide

/// Fork in `ChatSessionModel`: a double tap sends one `fork`, the screen
/// takes the new session once, and a fork the agent does not offer is never
/// sent (the user is told).
@MainActor
final class ChatSessionModelForkTests: XCTestCase {

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

    /// The `fork` commands sent so far.
    private func forks(_ connection: FakeWebSocketConnection) throws -> [[String: JSONValue]] {
        try connection.sentBinaries
            .filter { $0.first == ChatFrameType.command }
            .map { try WireJSON.decoder().decode(JSONValue.self, from: Data($0.dropFirst())) }
            .compactMap { value -> [String: JSONValue]? in
                guard case .object(let fields) = value, fields["op"] == .string("fork") else { return nil }
                return fields
            }
    }

    func testADoubleTapSendsOneForkAndTheScreenTakesTheNewSessionOnce() async throws {
        let live = try await liveModel(stateJSON: #""status":"ready","fork":{"chat":true,"at_answer":true}"#)
        defer { live.model.disconnect() }

        live.model.fork(itemId: "m:4")
        live.model.fork(itemId: "m:4")
        await waitUntil { (try? self.forks(live.connection).count) == 1 }

        XCTAssertTrue(live.model.forking)
        XCTAssertFalse(live.model.canFork)
        XCTAssertNil(live.model.message)
        let fork = try XCTUnwrap(forks(live.connection).first)
        XCTAssertEqual(fork["item_id"], .string("m:4"))
        guard case .string(let id)? = fork["id"] else { return XCTFail("the fork has no id") }
        emit(live.connection, #"{"t":"result","id":"\#(id)","ok":true,"data":{"session_id":"chat-2","session":"#
            + #"{"session_id":"chat-2","state":"running","created_at":"2026-11-27T00:00:01Z","ui":"chat"}}}"#)
        await waitUntil { !live.model.forking }

        let opened = try XCTUnwrap(live.model.takeOpenedFork())
        XCTAssertEqual(opened.sessionId, "chat-2")
        XCTAssertEqual(opened.session?.session_id, "chat-2")
        XCTAssertNil(live.model.takeOpenedFork())
        XCTAssertNil(live.model.message)
    }

    func testAnAgentThatForksOnlyTheWholeChatGetsNoForkAtAnAnswerAndAFailureShowsTheAgentsError() async throws {
        let live = try await liveModel(stateJSON: #""status":"ready","fork":{"chat":true,"at_answer":false}"#)
        defer { live.model.disconnect() }
        let busy = "Wait for the turn to end, or stop it, then fork the chat."

        XCTAssertFalse(live.model.offersForkAtAnswer)
        live.model.fork(itemId: "m:4")
        XCTAssertFalse(live.model.forking)
        XCTAssertEqual(live.model.message?.text, "Could not fork the chat now. Try again.")
        XCTAssertEqual(try forks(live.connection).count, 0)

        live.model.fork(itemId: nil)
        await waitUntil { (try? self.forks(live.connection).count) == 1 }
        let fork = try XCTUnwrap(forks(live.connection).first)
        XCTAssertNil(fork["item_id"])
        guard case .string(let id)? = fork["id"] else { return XCTFail("the fork has no id") }
        emit(live.connection, #"{"t":"result","id":"\#(id)","ok":false,"error":"\#(busy)"}"#)
        await waitUntil { !live.model.forking }

        XCTAssertEqual(live.model.message?.text, busy)
        XCTAssertEqual(live.model.message?.tone, .danger)
        XCTAssertNil(live.model.takeOpenedFork())
    }
}
