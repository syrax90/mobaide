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
    /// A PreferencesStore over a unique UserDefaults suite so tests never
    /// bleed persisted state into each other.
    static func isolatedPreferencesStore() -> PreferencesStore {
        PreferencesStore(defaults: UserDefaults(suiteName: "terminal-tests-\(UUID().uuidString)")!)
    }

    /// CentralAPI routed through `StubURLProtocol` — call
    /// `StubURLProtocol.reset()` in `setUp` and enqueue responses per test.
    static func stubbedCentralAPI() -> CentralAPI {
        CentralAPI(
            centralServerURL: "https://central.test",
            sessionInvalidator: StubSessionInvalidator(),
            client: HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        )
    }

    static func shortcutsRepository(harness: SessionHarness) -> ShortcutsRepository {
        ShortcutsRepository(centralAPI: stubbedCentralAPI(), userIdentityStore: harness.userIdentityStore)
    }

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
            foregroundSessionTracker: ForegroundSessionTracker(),
            preferencesStore: isolatedPreferencesStore(),
            shortcutsRepository: shortcutsRepository(harness: harness)
        )
    }

    static func model(
        sessionId: String = "s1",
        kind: SessionKind = .claude,
        resolvedKind: SessionKind? = nil,
        initialStatus: String? = nil,
        transportFactory: FakeWebSocketTransportFactory = FakeWebSocketTransportFactory(),
        harness: SessionHarness = SessionHarness(userLoggedIn: true, agentConnected: true),
        preferencesStore: PreferencesStore? = nil,
        shortcutsRepository: ShortcutsRepository? = nil,
        kindResolver: ((String) async -> SessionKind?)? = nil
    ) -> TerminalSessionModel {
        TerminalSessionModel(
            sessionId: sessionId,
            kind: kind,
            resolvedKind: resolvedKind,
            initialStatus: initialStatus,
            webSocketFactory: WebSocketCoreFactory.live(
                transportFactory: transportFactory,
                sleeper: RecordingSleeper()
            ),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore,
            centralAPI: CentralAPI(centralServerURL: "https://central.test", sessionInvalidator: StubSessionInvalidator()),
            userTokenProvider: StubUserTokenProvider(),
            foregroundSessionTracker: ForegroundSessionTracker(),
            preferencesStore: preferencesStore ?? isolatedPreferencesStore(),
            shortcutsRepository: shortcutsRepository ?? Self.shortcutsRepository(harness: harness),
            kindResolver: kindResolver ?? { _ in nil }
        )
    }
}

/// Small ShortcutDto builder for repository/model tests (Android `dto(...)` parity).
func makeShortcut(
    id: String,
    kind: String = "claude",
    chordToken: String,
    name: String? = nil,
    position: Int = 0,
    createdAt: String = "2026-07-20T12:00:00Z",
    version: Int = 1
) -> ShortcutDto {
    ShortcutDto(
        shortcut_id: id,
        kind: kind,
        chord_token: chordToken,
        name: name,
        position: position,
        created_at: createdAt,
        updated_at: createdAt,
        version: version
    )
}
