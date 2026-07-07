import Foundation

/// App-scoped cache of `TerminalSessionModel`s keyed by session id. Because the
/// models live here — not in a screen's `@State` — the emulator and WebSocket
/// connection survive SwiftUI view teardown/recreation (navigating away and
/// back, rotation) and only reset when the session is evicted.
@MainActor
final class TerminalSessionStore {
    private let webSocketFactory: WebSocketCoreFactory
    private let sessionManager: SessionManager
    private let tokenStore: TokenStore
    private let centralAPI: CentralAPI
    private let userTokenProvider: any UserTokenProviding
    private let foregroundSessionTracker: ForegroundSessionTracker

    private var models: [String: TerminalSessionModel] = [:]

    init(
        webSocketFactory: WebSocketCoreFactory,
        sessionManager: SessionManager,
        tokenStore: TokenStore,
        centralAPI: CentralAPI,
        userTokenProvider: any UserTokenProviding,
        foregroundSessionTracker: ForegroundSessionTracker
    ) {
        self.webSocketFactory = webSocketFactory
        self.sessionManager = sessionManager
        self.tokenStore = tokenStore
        self.centralAPI = centralAPI
        self.userTokenProvider = userTokenProvider
        self.foregroundSessionTracker = foregroundSessionTracker
    }

    func model(id: String, kind: SessionKind, initialStatus: String?) -> TerminalSessionModel {
        if let existing = models[id] {
            return existing
        }
        let model = TerminalSessionModel(
            sessionId: id,
            kind: kind,
            initialStatus: initialStatus,
            webSocketFactory: webSocketFactory,
            sessionManager: sessionManager,
            tokenStore: tokenStore,
            centralAPI: centralAPI,
            userTokenProvider: userTokenProvider,
            foregroundSessionTracker: foregroundSessionTracker
        )
        models[id] = model
        model.start()
        return model
    }

    func evict(_ id: String) {
        models.removeValue(forKey: id)?.disconnect()
    }
}
