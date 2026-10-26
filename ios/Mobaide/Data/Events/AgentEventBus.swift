import Foundation

/// Port of Android `AgentEventBus`: singleton fan-out of the agent-scoped
/// `ws/events` channel into typed `AgentEvent`s.
///
/// Lifecycle invariants:
/// - Runs only while `fullyConnected`, **including the replayed initial
///   auth state** (Android deliberately does NOT drop(1) here — dropping
///   the replayed value swallowed cold-start `fullyConnected` and left the
///   bus dead until the next transition). Every other state stops it.
/// - The events consumer attaches to `WebSocketCore.events` *before*
///   `connect()` (Android `CoroutineStart.UNDISPATCHED` parity) so the
///   initial burst — connected + `sessions.changed` + `projects.changed` —
///   is never lost.
/// - A fresh `WebSocketCore` is built per start/stop cycle; its
///   single-consumer stream cannot be re-iterated.
///
/// Broadcast: per-subscriber streams with no replay (Android SharedFlow
/// replay=0) — subscribers receive every event emitted while subscribed;
/// late subscribers get no history.
actor AgentEventBus {
    private let webSocketFactory: WebSocketCoreFactory
    private let sessionManager: SessionManager
    private let tokenStore: TokenStore

    private let eventsBroadcaster = Broadcaster<AgentEvent>()
    private let connectionBroadcaster = Broadcaster<BusConnectionState>.stateFlow()

    private var core: WebSocketCore?
    private var consumeTask: Task<Void, Never>?
    private var lifecycleTask: Task<Void, Never>?
    private var unknownEnvelopeTypeTotal = 0

    init(
        webSocketFactory: WebSocketCoreFactory,
        sessionManager: SessionManager,
        tokenStore: TokenStore
    ) {
        self.webSocketFactory = webSocketFactory
        self.sessionManager = sessionManager
        self.tokenStore = tokenStore
        connectionBroadcaster.emit(.disconnected)
    }

    nonisolated func events() -> AsyncStream<AgentEvent> {
        eventsBroadcaster.subscribe()
    }

    nonisolated func connectionStates() -> AsyncStream<BusConnectionState> {
        connectionBroadcaster.subscribe()
    }

    nonisolated var currentConnectionState: BusConnectionState {
        connectionBroadcaster.latestValue ?? .disconnected
    }

    var unknownEnvelopeCount: Int { unknownEnvelopeTypeTotal }

    /// Idempotent. Binds the lifecycle to auth-state transitions; the
    /// replayed current state performs the initial start when already
    /// `fullyConnected`.
    func start() {
        guard lifecycleTask == nil else { return }
        let stream = sessionManager.authStates()
        lifecycleTask = Task { [weak self] in
            for await state in stream {
                guard let self else { return }
                switch state {
                case .fullyConnected:
                    await self.startConnection()
                case .unauthenticated, .userOnly, .incompatibleServer:
                    await self.stopConnection()
                }
            }
        }
    }

    /// Tear down and immediately re-establish the connection (fresh server
    /// snapshot on pull-to-refresh / screen-on).
    func forceReconnect() async {
        await stopConnection()
        await startConnection()
    }

    private func startConnection() async {
        guard core == nil else { return }
        let loggedIn = await sessionManager.isLoggedIn()
        guard core == nil else { return }
        guard loggedIn else {
            connectionBroadcaster.emit(.disconnected)
            return
        }

        connectionBroadcaster.emit(.connecting)
        let core = webSocketFactory.make(
            Self.urlProvider(tokenStore: tokenStore, sessionManager: sessionManager),
            { [sessionManager] in await sessionManager.isLoggedIn() }
        )
        self.core = core
        consumeTask = Task { [weak self] in
            for await event in core.events {
                guard let self else { return }
                await self.handle(event)
            }
        }
        await core.connect()
    }

    private func stopConnection() async {
        consumeTask?.cancel()
        consumeTask = nil
        if let core {
            self.core = nil
            await core.disconnect()
        }
        connectionBroadcaster.emit(.disconnected)
    }

    private func handle(_ event: WebSocketEvent) async {
        switch event {
        case .connected:
            connectionBroadcaster.emit(.connected)
        case .disconnected, .error:
            connectionBroadcaster.emit(.connecting)
        case .message(let text):
            parseAndEmit(text)
        case .binary:
            break
        case .tokenRejected, .accountRemoved, .incompatibleServer:
            connectionBroadcaster.emit(.disconnected)
        }
    }

    private func parseAndEmit(_ text: String) {
        guard let event = AgentEnvelopeDemux.demux(text) else { return }
        if case .unknownEnvelopeType = event {
            unknownEnvelopeTypeTotal += 1
        }
        eventsBroadcaster.emit(event)
    }

    /// Re-evaluated on every (re)connect attempt. Pre-connect refresh of an
    /// expiring token; a terminal refresh abandons the attempt (nil URL —
    /// the auth-state collector stops the bus separately).
    private static func urlProvider(
        tokenStore: TokenStore,
        sessionManager: SessionManager
    ) -> @Sendable () async throws -> String? {
        {
            guard await sessionManager.isLoggedIn() else { return nil }
            if tokenStore.isAccessTokenExpiringSoon() {
                if await sessionManager.refresh() == .terminal { return nil }
            }
            guard let token = tokenStore.getAccessToken() else { return nil }
            let mode = tokenStore.getConnectionMode()
            let base: String?
            switch mode {
            case .direct:
                base = tokenStore.getAgentUrl()
            case .relay:
                base = tokenStore.getRelayServerUrl()
            }
            guard let base else { return nil }
            return RelayRouting.webSocketURLString(
                baseURL: base,
                path: "ws/events",
                mode: mode,
                agentId: tokenStore.getAgentId(),
                token: token
            )
        }
    }
}
