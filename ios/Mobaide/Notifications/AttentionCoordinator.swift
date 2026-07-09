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
    private var reconcileTask: Task<Void, Never>?
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
            // The event and auth loops are independent tasks: a buffered
            // attention event processed after an agent-leave teardown must not
            // re-post the previous agent's alert. (nil = no auth observed yet →
            // the event itself implies a live connection, so post.)
            if let latest = latestAuthState, latest != .fullyConnected { return }
            await presenter.post(session)
        case .sessionUpdated(let session) where session.attention == nil:
            presenter.remove(sessionId: session.session_id)
        default:
            break
        }
    }

    /// Authorization + agent-scoped notification lifecycle, driven by the auth
    /// state. On reaching `.fullyConnected`: request provisional authorization
    /// once, then reconcile — so attention that accrued while the app wasn't
    /// running (a cold launch after iOS killed a suspended app) or while the
    /// agent was disconnected is posted, since the `.active` path only
    /// reconciles a background→foreground resume and the connect snapshot
    /// (`sessions.changed`) is ignored here. Notifications are agent-scoped, so
    /// drop every alert whenever the agent connection is lost — a logout, or an
    /// agent disconnect/switch/takeover leaving `.fullyConnected` — otherwise
    /// the previous agent's alerts linger and deep-link to a session the next
    /// agent can't resolve. A `.userOnly` seen while still connecting (cold
    /// start, not a downgrade) keeps any cross-launch alerts. Mirrors
    /// `Router.handleTransition`'s stack reset.
    private func handleAuth(_ state: AuthState) async {
        let previous = latestAuthState
        latestAuthState = state
        switch state {
        case .fullyConnected:
            if !didRequestAuthorization {
                didRequestAuthorization = true
                await presenter.requestProvisionalAuthorization()
            }
            // Reconcile off the serial auth loop so a subsequent teardown state
            // (logout / agent switch) isn't blocked behind the network snapshot.
            reconcileCancellable()
        case .userOnly, .unauthenticated, .incompatibleServer:
            if previous == .fullyConnected || state == .unauthenticated {
                reconcileTask?.cancel()
                presenter.removeAll()
            }
        }
    }

    /// Foreground reconciliation between the snapshot-derived attention set A
    /// and the delivered set B: post A\B (attention the client never showed)
    /// and remove B\A (alerts whose attention is gone).
    /// Run a reconcile through the single cancellable slot, superseding any
    /// in-flight one, so an agent-leave teardown's `reconcileTask?.cancel()`
    /// reaches it — used by both the connect path and the foreground resume.
    func reconcileCancellable() {
        reconcileTask?.cancel()
        reconcileTask = Task { [weak self] in await self?.reconcile() }
    }

    func reconcile() async {
        // Read the delivered set BEFORE the REST snapshot: a live
        // `.sessionAttention` that posts during the snapshot await would
        // otherwise appear "delivered but absent from the older attention set"
        // and be wrongly removed by the B\A loop below.
        let delivered = await presenter.deliveredSessionIds()
        guard let sessions = try? await snapshot() else { return }
        // A teardown may have cancelled this reconcile while the snapshot was in
        // flight — don't resurrect the previous agent's alerts after removeAll().
        if Task.isCancelled { return }
        let attentionSet = AttentionContent.attentionSessionIds(in: sessions)
        for sessionId in attentionSet.subtracting(delivered) {
            // Re-check per iteration: a teardown landing mid-loop must not keep
            // posting the previous agent's alerts after removeAll() ran.
            if Task.isCancelled { return }
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
