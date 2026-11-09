import XCTest
@testable import Mobaide

/// `ChatSessionStore` against the agent's session list. A session that leaves
/// the list is evicted, and the model an open chat screen still holds must read
/// as closed — an evicted model is disconnected, and without the closed mark
/// the screen shows "Reconnecting…" forever.
@MainActor
final class ChatSessionStoreTests: XCTestCase {

    private func makeStore() -> ChatSessionStore {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let tracker = ForegroundSessionTracker()
        return ChatSessionStore(
            webSocketFactory: WebSocketCoreFactory.live(
                transportFactory: FakeWebSocketTransportFactory(),
                sleeper: RecordingSleeper()
            ),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore,
            foregroundSessionTracker: tracker,
            reviewPrompt: TerminalTestSupport.reviewPrompt(
                preferencesStore: TerminalTestSupport.isolatedPreferencesStore(),
                foregroundSessionTracker: tracker
            )
        )
    }

    func testASessionThatLeavesTheListIsEvictedAndReadsAsClosed() async {
        let store = makeStore()
        let (sessions, continuation) = AsyncStream.makeStream(of: [SessionResponse].self)
        defer { continuation.finish() }
        store.start(sessions: sessions)
        continuation.yield([makeSessionResponse(id: "gone"), makeSessionResponse(id: "kept")])
        let gone = store.model(id: "gone")
        let kept = store.model(id: "kept")
        let unlisted = store.model(id: "unlisted")
        await waitUntil { !store.hasModel("unlisted") }

        continuation.yield([makeSessionResponse(id: "kept")])
        await waitUntil { !store.hasModel("gone") }

        XCTAssertFalse(store.hasModel("gone"))
        XCTAssertTrue(gone.sessionClosed)
        XCTAssertTrue(store.hasModel("kept"))
        XCTAssertFalse(kept.sessionClosed)
        // Never listed: evicted, but not shown as closed.
        XCTAssertFalse(unlisted.sessionClosed)
    }
}
