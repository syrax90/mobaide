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
}
