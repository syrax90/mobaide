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
    private let reviewPrompt: ReviewPromptCoordinator

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
        shortcutsRepository: ShortcutsRepository,
        reviewPrompt: ReviewPromptCoordinator
    ) {
        self.webSocketFactory = webSocketFactory
        self.sessionManager = sessionManager
        self.tokenStore = tokenStore
        self.centralAPI = centralAPI
        self.userTokenProvider = userTokenProvider
        self.foregroundSessionTracker = foregroundSessionTracker
        self.preferencesStore = preferencesStore
        self.shortcutsRepository = shortcutsRepository
        self.reviewPrompt = reviewPrompt
    }

    func hasModel(_ id: String) -> Bool {
        models[id] != nil
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
            reviewPrompt: reviewPrompt,
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

    /// Adopt a session's kind into an already-live model (idempotent — only
    /// the first resolution wins). Fed by `SessionRepository` on every
    /// snapshot, so a model whose one-shot cold-start resolution failed
    /// self-heals from the next `sessions.changed` push instead of keeping
    /// the neutral key bar forever (Android's live session-list merge parity).
    func adoptKind(id: String, kind: SessionKind) {
        models[id]?.adoptResolvedKind(kind)
    }

    func evict(_ id: String) {
        models.removeValue(forKey: id)?.disconnect()
    }

    /// A session-scoped child screen (git Changes / Diff) keeps the covered
    /// terminal watched for as long as it is on screen; see
    /// `ForegroundSessionTracker.holdWatch`. Routed through the store because
    /// the tracker has no observation channel and the model is main-actor.
    func holdWatch(sessionId: String) {
        foregroundSessionTracker.holdWatch(sessionId)
        models[sessionId]?.syncWatchHold()
    }

    func releaseWatch(sessionId: String) {
        foregroundSessionTracker.releaseWatch(sessionId)
        models[sessionId]?.syncWatchHold()
    }

    /// Publish the app's foreground state to every retained model.
    ///
    /// The models are app-scoped and outlive their views, so without this a
    /// backgrounded app keeps a live PTY stream for every session the user
    /// has ever opened.
    func setAppActive(_ active: Bool) {
        for model in models.values {
            model.setAppActive(active)
        }
    }
}
