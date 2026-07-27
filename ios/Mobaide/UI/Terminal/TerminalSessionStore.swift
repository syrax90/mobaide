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
    private let preferencesStore: PreferencesStore
    private let shortcutsRepository: ShortcutsRepository

    /// Resolves a session's true kind from the app-scoped session list.
    /// Assigned by `AppContainer` right after `SessionRepository` is built —
    /// the repository takes this store in its own init (for eviction), so it
    /// cannot be a constructor dependency here. Weak-captured there to avoid
    /// the store → closure → repository → store retain cycle.
    var sessionKindResolver: (String) async -> SessionKind? = { _ in nil }

    private var models: [String: TerminalSessionModel] = [:]

    init(
        webSocketFactory: WebSocketCoreFactory,
        sessionManager: SessionManager,
        tokenStore: TokenStore,
        centralAPI: CentralAPI,
        userTokenProvider: any UserTokenProviding,
        foregroundSessionTracker: ForegroundSessionTracker,
        preferencesStore: PreferencesStore,
        shortcutsRepository: ShortcutsRepository
    ) {
        self.webSocketFactory = webSocketFactory
        self.sessionManager = sessionManager
        self.tokenStore = tokenStore
        self.centralAPI = centralAPI
        self.userTokenProvider = userTokenProvider
        self.foregroundSessionTracker = foregroundSessionTracker
        self.preferencesStore = preferencesStore
        self.shortcutsRepository = shortcutsRepository
    }

    func model(
        id: String,
        kind: SessionKind,
        resolvedKind: SessionKind?,
        initialStatus: String?
    ) -> TerminalSessionModel {
        if let existing = models[id] {
            // A model created before the session row loaded (cold-start deep
            // link) resolved no kind; adopt it once the caller knows better.
            if let resolvedKind {
                existing.adoptResolvedKind(resolvedKind)
            }
            return existing
        }
        let model = TerminalSessionModel(
            sessionId: id,
            kind: kind,
            resolvedKind: resolvedKind,
            initialStatus: initialStatus,
            webSocketFactory: webSocketFactory,
            sessionManager: sessionManager,
            tokenStore: tokenStore,
            centralAPI: centralAPI,
            userTokenProvider: userTokenProvider,
            foregroundSessionTracker: foregroundSessionTracker,
            preferencesStore: preferencesStore,
            shortcutsRepository: shortcutsRepository,
            // Indirection instead of passing `sessionKindResolver` directly:
            // the resolver is assigned after this store's init, and models must
            // see the late assignment.
            kindResolver: { [weak self] sessionId in
                guard let resolver = self?.sessionKindResolver else { return nil }
                return await resolver(sessionId)
            }
        )
        models[id] = model
        model.start()
        return model
    }

    func evict(_ id: String) {
        models.removeValue(forKey: id)?.disconnect()
    }
}
