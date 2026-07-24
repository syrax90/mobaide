import Foundation
@testable import Mobaide

struct StubSessionInvalidator: SessionInvalidating {
    func invalidateUserSession() async {}
}

struct StubUserTokenProvider: UserTokenProviding {
    func userToken() async -> String? { "user-token" }
}

/// Shared builders for the terminal/session test stacks.
@MainActor
enum TerminalTestSupport {
    static func store(_ harness: SessionHarness) -> TerminalSessionStore {
        TerminalSessionStore(
            webSocketFactory: WebSocketCoreFactory.live(
                transportFactory: FakeWebSocketTransportFactory(),
                sleeper: RecordingSleeper()
            ),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore,
            centralAPI: CentralAPI(centralServerURL: "https://central.test", sessionInvalidator: StubSessionInvalidator()),
            userTokenProvider: StubUserTokenProvider(),
            foregroundSessionTracker: ForegroundSessionTracker()
        )
    }

    static func model(
        sessionId: String = "s1",
        kind: SessionKind = .claude,
        initialStatus: String? = nil,
        transportFactory: FakeWebSocketTransportFactory = FakeWebSocketTransportFactory(),
        harness: SessionHarness = SessionHarness(userLoggedIn: true, agentConnected: true)
    ) -> TerminalSessionModel {
        TerminalSessionModel(
            sessionId: sessionId,
            kind: kind,
            initialStatus: initialStatus,
            webSocketFactory: WebSocketCoreFactory.live(
                transportFactory: transportFactory,
                sleeper: RecordingSleeper()
            ),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore,
            centralAPI: CentralAPI(centralServerURL: "https://central.test", sessionInvalidator: StubSessionInvalidator()),
            userTokenProvider: StubUserTokenProvider(),
            foregroundSessionTracker: ForegroundSessionTracker()
        )
    }
}
