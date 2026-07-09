import Foundation

/// Drives local notifications off the agent event bus (port of the delivery
/// loop in Android `SessionEventService`): a `session.attention` one-shot
/// posts, a `session.updated` that clears attention removes. On foreground it
/// reconciles delivered notifications against a fresh REST snapshot so events
/// missed while the app was suspended still surface — iOS has no
/// foreground-service equivalent to keep the socket alive. Mirrors
/// `SessionRepository`'s subscribe-before-the-bus-starts pattern.
@MainActor
final class AttentionCoordinator {
    private let eventStream: @Sendable () -> AsyncStream<AgentEvent>
    private let authStates: @Sendable () -> AsyncStream<AuthState>
    private let presenter: any AttentionPresenting
    private let snapshot: @Sendable () async throws -> [SessionResponse]

    private var eventSubscription: Task<Void, Never>?
    private var authSubscription: Task<Void, Never>?
    private var didRequestAuthorization = false
    private var latestAuthState: AuthState?

    init(
        eventStream: @escaping @Sendable () -> AsyncStream<AgentEvent>,
        authStates: @escaping @Sendable () -> AsyncStream<AuthState>,
        presenter: any AttentionPresenting,
        snapshot: @escaping @Sendable () async throws -> [SessionResponse]
    ) {
        self.eventStream = eventStream
        self.authStates = authStates
        self.presenter = presenter
        self.snapshot = snapshot
    }

    /// Idempotent. Subscribe before `AgentEventBus.start()` so the snapshot
    /// the bus re-pushes on connect is not missed (events have no replay).
    func start() {
        guard eventSubscription == nil else { return }
        let events = eventStream()
        eventSubscription = Task { [weak self] in
            for await event in events {
                await self?.apply(event)
            }
        }
        let states = authStates()
        authSubscription = Task { [weak self] in
            for await state in states {
                await self?.handleAuth(state)
            }
        }
    }

    private func apply(_ event: AgentEvent) async {
        switch event {
        case .sessionAttention(let session):
            await presenter.post(session)
        case .sessionUpdated(let session) where session.attention == nil:
            presenter.remove(sessionId: session.session_id)
        default:
            break
        }
    }

    /// Request provisional authorization once, on the first `.fullyConnected`
    /// (the agent is live and attention events can start flowing). Notifications
    /// are agent-scoped, so drop every alert whenever the agent connection is
    /// lost — a logout, or an agent disconnect/switch/takeover that leaves
    /// `.fullyConnected` — otherwise the previous agent's session alerts linger
    /// and deep-link to a session the next agent can't resolve. A `.userOnly`
    /// seen while still connecting (cold start, not a downgrade) keeps any
    /// cross-launch alerts. Mirrors `Router.handleTransition`'s stack reset.
    private func handleAuth(_ state: AuthState) async {
        let previous = latestAuthState
        latestAuthState = state
        switch state {
        case .fullyConnected:
            guard !didRequestAuthorization else { return }
            didRequestAuthorization = true
            await presenter.requestProvisionalAuthorization()
        case .userOnly, .unauthenticated, .incompatibleServer:
            if previous == .fullyConnected || state == .unauthenticated {
                presenter.removeAll()
            }
        }
    }

    /// Foreground reconciliation between the snapshot-derived attention set A
    /// and the delivered set B: post A\B (attention the client never showed)
    /// and remove B\A (alerts whose attention is gone).
    func reconcile() async {
        guard let sessions = try? await snapshot() else { return }
        let attentionSet = AttentionContent.attentionSessionIds(in: sessions)
        let delivered = await presenter.deliveredSessionIds()
        for sessionId in attentionSet.subtracting(delivered) {
            guard let session = sessions.first(where: { $0.session_id == sessionId }) else { continue }
            await presenter.post(session)
        }
        // B\A: attention resolved elsewhere, or the session was deleted. A
        // still-listed `ended` session keeps attention=="ended" and stays in
        // A; only a session deleted server-side before its "ended" alert was
        // seen is cleared here, which is acceptable — it no longer exists.
        for sessionId in delivered.subtracting(attentionSet) {
            presenter.remove(sessionId: sessionId)
        }
    }
}
