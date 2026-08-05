import Foundation

/// Drives local notifications off the agent event bus (port of the delivery
/// loop in Android `SessionEventService`): a `session.attention` one-shot
/// posts, a `session.updated` that clears attention removes. On foreground it
/// reconciles a fresh REST snapshot against `AttentionSeenStore` so events
/// missed while the app was suspended still surface exactly once — iOS has no
/// foreground-service equivalent to keep the socket alive. Mirrors
/// `SessionRepository`'s subscribe-before-the-bus-starts pattern.
@MainActor
final class AttentionCoordinator {
    private let eventStream: @Sendable () -> AsyncStream<AgentEvent>
    private let authStates: @Sendable () -> AsyncStream<AuthState>
    private let presenter: any AttentionPresenting
    private let snapshot: @Sendable () async throws -> [SessionResponse]
    private let seen: AttentionSeenStore

    private var eventSubscription: Task<Void, Never>?
    private var authSubscription: Task<Void, Never>?
    private var reconcileTask: Task<Void, Never>?
    private var didRequestAuthorization = false
    private var latestAuthState: AuthState?

    init(
        eventStream: @escaping @Sendable () -> AsyncStream<AgentEvent>,
        authStates: @escaping @Sendable () -> AsyncStream<AuthState>,
        presenter: any AttentionPresenting,
        snapshot: @escaping @Sendable () async throws -> [SessionResponse],
        seen: AttentionSeenStore
    ) {
        self.eventStream = eventStream
        self.authStates = authStates
        self.presenter = presenter
        self.snapshot = snapshot
        self.seen = seen
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
            // Remembering a live alert is what stops the next foreground
            // reconcile from re-posting it once the user dismisses it.
            if await presenter.post(session) { seen.record(session) }
        case .sessionUpdated(let session) where session.attention == nil:
            presenter.remove(sessionId: session.session_id)
            seen.forget(sessionId: session.session_id)
        default:
            break
        }
    }

    /// Authorization + agent-scoped notification lifecycle, driven by the auth
    /// state. On reaching `.fullyConnected`: request notification authorization
    /// once — off this loop, because the system prompt blocks on the user and
    /// this loop is serial, so awaiting it would hold a teardown state behind
    /// the dialog — then reconcile, so attention that accrued while the app wasn't
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
                // Synchronous: started in this same loop turn, so a teardown
                // can never interleave and leave a prompt opening after it.
                presenter.requestAuthorization()
            }
            // Reconcile off the serial auth loop so a subsequent teardown state
            // (logout / agent switch) isn't blocked behind the network snapshot.
            reconcileCancellable()
        case .userOnly, .unauthenticated, .incompatibleServer:
            if previous == .fullyConnected || state == .unauthenticated {
                reconcileTask?.cancel()
                presenter.removeAll()
                // Sign-out drops the surfaced-attention memory with the
                // account. An agent switch keeps it: those alerts were
                // genuinely shown, and re-selecting the agent must not replay
                // them.
                if state == .unauthenticated { seen.clearAll() }
            }
        }
    }

    /// Called when the user grants notification authorization, from whichever
    /// request happened to be in flight — the one this coordinator makes on
    /// connect, or a retry `post` starts on finding `.notDetermined`. `post`
    /// drops alerts outright while authorization is undecided, so reconciling
    /// here is what recovers them; sourcing that from a fresh snapshot means
    /// attention resolved while the prompt was up is simply absent rather than
    /// posted late. Ignored once the agent connection is gone: a prompt answered
    /// after a logout or agent switch must not re-post the previous agent's
    /// alerts past `removeAll()`.
    func authorizationGranted() {
        guard latestAuthState == .fullyConnected else { return }
        reconcileCancellable()
    }

    /// Run a reconcile through the single cancellable slot, superseding any
    /// in-flight one, so an agent-leave teardown's `reconcileTask?.cancel()`
    /// reaches it — used by both the connect path and the foreground resume.
    func reconcileCancellable() {
        reconcileTask?.cancel()
        reconcileTask = Task { [weak self] in await self?.reconcile() }
    }

    /// Foreground reconciliation of the snapshot-derived attention set against
    /// what this device has already surfaced: post the instances it has not,
    /// and remove alerts whose attention is gone.
    ///
    /// The snapshot carries *latched* attention — the agent clears it only on a
    /// client PTY write or a `UserPromptSubmit`, so a session nobody answers
    /// reports the same reason indefinitely. Which is why "already shown" is
    /// answered by `AttentionSeenStore` and not by the delivered set: the user
    /// tapping or swiping the alert empties Notification Center, and reading
    /// only that would re-post the same stale alert on every app open.
    func reconcile() async {
        // Read the delivered set BEFORE the REST snapshot: a live
        // `.sessionAttention` that posts during the snapshot await would
        // otherwise appear "delivered but absent from the older attention set"
        // and be wrongly removed by the removal loop below.
        let delivered = await presenter.deliveredSessionIds()
        guard let sessions = try? await snapshot() else { return }
        // A teardown may have cancelled this reconcile while the snapshot was in
        // flight — don't resurrect the previous agent's alerts after removeAll().
        if Task.isCancelled { return }
        let attentionSessions = AttentionContent.attentionSessions(in: sessions)
        let attentionSet = Set(attentionSessions.map(\.session_id))
        // Attention cleared while this device was away leaves no event to
        // observe, so the snapshot is the only place that re-arms the session.
        seen.retain(sessionIds: attentionSet)
        if seen.isBaselined {
            for session in attentionSessions {
                // Deliberately not recorded as surfaced: an APNs push leaves no
                // local record and its identifier is the session id, so this
                // alert may be an older instance's — muting on it would suppress
                // an instance this device never rendered.
                if delivered.contains(session.session_id) { continue }
                if seen.hasSurfaced(session) { continue }
                // Re-check per iteration: a teardown landing mid-loop must not
                // keep posting the previous agent's alerts after removeAll() ran.
                if Task.isCancelled { return }
                if await presenter.post(session) { seen.record(session) }
            }
        } else {
            // First reconcile for this agent: adopt the current state as the
            // baseline rather than replaying history. A device with no record
            // has missed nothing it can act on, and everything latched is
            // already visible in the session list of the app just opened.
            seen.adoptBaseline(attentionSessions)
        }
        // Attention resolved elsewhere, or the session was deleted. A
        // still-listed `ended` session keeps attention=="ended" and stays in
        // the attention set; only a session deleted server-side before its
        // "ended" alert was seen is cleared here, which is acceptable.
        for sessionId in delivered.subtracting(attentionSet) {
            presenter.remove(sessionId: sessionId)
        }
    }
}
