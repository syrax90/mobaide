import XCTest
@testable import Mobaide

/// Visibility of `ChatSessionModel`: two chat screens can show one model at
/// once, so it counts screens and stays visible until the last one hides; a
/// repeated scene-phase report does not restart a socket that is connecting.
@MainActor
final class ChatSessionModelVisibilityTests: XCTestCase {

    private struct Stack {
        let model: ChatSessionModel
        let factory: FakeWebSocketTransportFactory
        let tracker: ForegroundSessionTracker
    }

    private func makeStack() -> Stack {
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
        return Stack(model: model, factory: factory, tracker: tracker)
    }

    func testTheModelStaysVisibleUntilTheLastOfTwoScreensHides() async throws {
        let stack = makeStack()
        defer { stack.model.disconnect() }
        stack.model.markVisible()
        let connection = try await stack.factory.connection(1)
        connection.emit(.opened)
        await waitUntil { stack.model.isLive }

        // A second screen of the same session appears before the first one goes.
        stack.model.markVisible()
        stack.model.markHidden()
        XCTAssertEqual(stack.tracker.visibleSessionId, "chat-1", "one screen still shows the chat")

        stack.model.markHidden()
        XCTAssertNil(stack.tracker.visibleSessionId)

        // A stray extra hide must not leave the count below zero.
        stack.model.markHidden()
        stack.model.markVisible()
        XCTAssertEqual(stack.tracker.visibleSessionId, "chat-1")
        let single = await stack.factory.assertNoConnection(beyond: 1)
        XCTAssertTrue(single, "visibility changes on a live chat must not open another socket")
    }

    func testARepeatedActiveReportDoesNotRestartAConnectingSocket() async throws {
        let stack = makeStack()
        defer { stack.model.disconnect() }
        stack.model.setAppActive(true)
        stack.model.markVisible()
        let connection = try await stack.factory.connection(1)

        // Every appearing screen reports the scene phase again.
        stack.model.setAppActive(true)

        let single = await stack.factory.assertNoConnection(beyond: 1)
        XCTAssertTrue(single, "an unchanged scene phase must not force a reconnect")
        XCTAssertTrue(connection.closeCalls.isEmpty)
    }
}
