import Foundation

/// App-scoped cache of `ChatSessionModel`s keyed by session id, so going back
/// and forth between sessions re-renders at once and only syncs the missed
/// events. Bounded: the least recently opened model past `maxModels` is
/// dropped, and a session leaving the agent's list is evicted.
@MainActor
final class ChatSessionStore {
    static let maxModels = 8

    private let webSocketFactory: WebSocketCoreFactory
    private let sessionManager: SessionManager
    private let tokenStore: TokenStore
    private let foregroundSessionTracker: ForegroundSessionTracker
    private let reviewPrompt: ReviewPromptCoordinator
    private var models: [String: ChatSessionModel] = [:]
    private var recency: [String] = []
    private var listedIds: Set<String> = []
    private var subscription: Task<Void, Never>?

    init(
        webSocketFactory: WebSocketCoreFactory,
        sessionManager: SessionManager,
        tokenStore: TokenStore,
        foregroundSessionTracker: ForegroundSessionTracker,
        reviewPrompt: ReviewPromptCoordinator
    ) {
        self.webSocketFactory = webSocketFactory
        self.sessionManager = sessionManager
        self.tokenStore = tokenStore
        self.foregroundSessionTracker = foregroundSessionTracker
        self.reviewPrompt = reviewPrompt
    }

    func start(sessions: AsyncStream<[SessionResponse]>) {
        guard subscription == nil else { return }
        subscription = Task { [weak self] in
            for await list in sessions {
                self?.retain(Set(list.map(\.session_id)))
            }
        }
    }

    func hasModel(_ id: String) -> Bool {
        models[id] != nil
    }

    func model(id: String) -> ChatSessionModel {
        recency.removeAll { $0 == id }
        recency.append(id)
        if let existing = models[id] {
            return existing
        }
        let model = ChatSessionModel(
            sessionId: id,
            webSocketFactory: webSocketFactory,
            sessionManager: sessionManager,
            tokenStore: tokenStore,
            foregroundSessionTracker: foregroundSessionTracker,
            reviewPrompt: reviewPrompt
        )
        models[id] = model
        while recency.count > Self.maxModels {
            evict(recency[0])
        }
        return model
    }

    func evict(_ id: String) {
        recency.removeAll { $0 == id }
        models.removeValue(forKey: id)?.disconnect()
    }

    private func retain(_ ids: Set<String>) {
        for id in models.keys where !ids.contains(id) {
            // Only a session that was listed and is now gone is closed: a
            // list never removes an entry except on an authoritative snapshot.
            if listedIds.contains(id) {
                models[id]?.markSessionClosed()
            }
            evict(id)
        }
        listedIds = ids
    }
}
