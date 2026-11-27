import XCTest
@testable import Mobaide

/// The predicted next prompt in `ChatSessionModel`: offered only while the chat
/// can take it now, a tap fills the composer and never sends, and a suggestion
/// the user used or typed over does not come back until the agent withdraws
/// the list for a new turn.
@MainActor
final class ChatSessionModelPromptSuggestionTests: XCTestCase {

    private struct Live {
        let model: ChatSessionModel
        let connection: FakeWebSocketConnection
    }

    private static let suggested = "Run the tests and fix what breaks"

    /// An image load the test finishes by hand.
    @MainActor
    private final class PendingLoad {
        private(set) var started = false
        private var continuation: CheckedContinuation<Data?, Never>?

        func load() async -> Data? {
            await withCheckedContinuation { continuation in
                self.continuation = continuation
                started = true
            }
        }

        func finish(_ data: Data?) {
            continuation?.resume(returning: data)
            continuation = nil
        }
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

    private func patch(_ connection: FakeWebSocketConnection, seq: Int, _ patchJSON: String) {
        emit(connection, #"{"t":"events","epoch":"e1","seq":\#(seq),"events":[{"seq":\#(seq),"type":"state","patch":{"#
            + patchJSON + #"}}]}"#)
    }

    private func sentPrompts(_ connection: FakeWebSocketConnection) throws -> Int {
        try connection.sentBinaries
            .filter { $0.first == ChatFrameType.command }
            .map { try WireJSON.decoder().decode(JSONValue.self, from: Data($0.dropFirst())) }
            .filter { value in
                guard case .object(let fields) = value else { return false }
                return fields["op"] == .string("prompt")
            }
            .count
    }

    private var offered: String {
        #""prompt_suggestions":[{"text":"\#(Self.suggested)"},{"text":"Commit it"}]"#
    }

    func testATapFillsTheComposerWithoutSendingAndTheSuggestionDoesNotComeBack() async throws {
        let live = try await liveModel(stateJSON: #""status":"ready","# + offered)
        defer { live.model.disconnect() }
        XCTAssertEqual(live.model.promptSuggestion, Self.suggested)

        live.model.applyPromptSuggestion(Self.suggested)

        XCTAssertEqual(live.model.composerText, Self.suggested)
        XCTAssertNil(live.model.promptSuggestion)
        XCTAssertEqual(try sentPrompts(live.connection), 0)

        live.model.composerText = ""
        XCTAssertNil(live.model.promptSuggestion, "a used suggestion must not come back")

        patch(live.connection, seq: 2, #""prompt_suggestions":[{"text":"Commit it"}]"#)
        await waitUntil { live.model.state.promptSuggestions == ["Commit it"] }
        XCTAssertEqual(live.model.promptSuggestion, "Commit it", "a new suggestion is offered")
    }

    func testTypingOverTheSuggestionSpendsIt() async throws {
        let live = try await liveModel(stateJSON: #""status":"ready","# + offered)
        defer { live.model.disconnect() }

        live.model.composerText = "f"
        XCTAssertNil(live.model.promptSuggestion)
        live.model.composerText = ""

        XCTAssertNil(live.model.promptSuggestion)
    }

    func testAWithdrawnListLetsTheNextTurnOfferTheSameTextAgain() async throws {
        let live = try await liveModel(stateJSON: #""status":"ready","# + offered)
        defer { live.model.disconnect() }
        live.model.applyPromptSuggestion(Self.suggested)
        live.model.composerText = ""
        XCTAssertNil(live.model.promptSuggestion)

        patch(live.connection, seq: 2, #""prompt_suggestions":[]"#)
        await waitUntil { live.model.state.promptSuggestions.isEmpty }
        patch(live.connection, seq: 3, offered)
        await waitUntil { !live.model.state.promptSuggestions.isEmpty }

        XCTAssertEqual(live.model.promptSuggestion, Self.suggested, "a new turn may predict the same prompt")
    }

    func testAnImageThatIsStillLoadingSpendsTheSuggestion() async throws {
        let live = try await liveModel(stateJSON: #""status":"ready","prompt_caps":{"image":true},"# + offered)
        defer { live.model.disconnect() }
        XCTAssertEqual(live.model.promptSuggestion, Self.suggested)
        let pending = PendingLoad()

        live.model.addImage { await pending.load() }
        await waitUntil { pending.started }
        XCTAssertEqual(live.model.preparingImages, 1)
        XCTAssertNil(live.model.promptSuggestion)

        pending.finish(nil)
        await waitUntil { live.model.preparingImages == 0 }
        XCTAssertTrue(live.model.attachments.isEmpty)
        XCTAssertNil(live.model.promptSuggestion, "the user started a draft, so the suggestion is spent")
    }

    func testNoSuggestionWhileATurnRunsOrAMessageIsQueued() async throws {
        let live = try await liveModel(stateJSON: #""status":"ready","# + offered)
        defer { live.model.disconnect() }

        patch(live.connection, seq: 2, #""status":"running""#)
        await waitUntil { live.model.phase == .running }
        XCTAssertNil(live.model.promptSuggestion)

        patch(live.connection, seq: 3, #""status":"ready","queue":[{"id":"q:1","text":"next","images":0}]"#)
        await waitUntil { live.model.phase == .ready && !live.model.state.queue.isEmpty }
        XCTAssertNil(live.model.promptSuggestion)

        patch(live.connection, seq: 4, #""queue":[]"#)
        await waitUntil { live.model.state.queue.isEmpty }
        XCTAssertEqual(live.model.promptSuggestion, Self.suggested, "only the gate hid it, so it shows again")
    }
}
