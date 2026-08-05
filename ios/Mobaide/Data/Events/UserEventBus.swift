import Foundation

/// Port of Android `UserEventBus`: the user-scoped `ws/user-events`
/// channel, routing template/agent-presence events straight into
/// repository hooks (no public event stream — the repositories' state is
/// the observable surface).
///
/// Lifecycle invariants:
/// - Runs while `userOnly` OR `fullyConnected`; stops on
///   `unauthenticated` / `incompatibleServer`.
/// - Reacts to auth-state **changes only** (Android `authState.drop(1)`):
///   the replayed initial value is ignored, and the initial start comes
///   from the explicit `start()` call at app launch.
/// - Terminal socket closures drive the session: 4001/HTTP-401 →
///   `invalidateUserSession(.tokenRejected)`, 4010 → `.accountRemoved`,
///   4002 → `markIncompatibleServer`.
/// - On every (re)connect it best-effort reconciles agents + templates via
///   REST, recovering events missed while the socket was down (unlike the
///   agent bus, which relies on the server re-pushing a snapshot).
actor UserEventBus {
    private let webSocketFactory: WebSocketCoreFactory
    private let sessionManager: SessionManager
    private let userIdentityStore: UserIdentityStore
    private let tokenStore: TokenStore
    private let deviceIdentityStore: DeviceIdentityStore
    private let agentStatusApplier: any AgentStatusApplying
    private let templateSync: any TemplateSyncApplying
    private let shortcutSync: any ShortcutSyncApplying
    private let customTypeSync: any CustomTypeSyncApplying

    private let connectionBroadcaster = Broadcaster<BusConnectionState>.stateFlow()

    private var core: WebSocketCore?
    private var consumeTask: Task<Void, Never>?
    private var lifecycleTask: Task<Void, Never>?

    init(
        webSocketFactory: WebSocketCoreFactory,
        sessionManager: SessionManager,
        userIdentityStore: UserIdentityStore,
        tokenStore: TokenStore,
        deviceIdentityStore: DeviceIdentityStore,
        agentStatusApplier: any AgentStatusApplying = NoOpAgentStatusApplier(),
        templateSync: any TemplateSyncApplying = NoOpTemplateSyncApplier(),
        shortcutSync: any ShortcutSyncApplying = NoOpShortcutSyncApplier(),
        customTypeSync: any CustomTypeSyncApplying = NoOpCustomTypeSyncApplier()
    ) {
        self.webSocketFactory = webSocketFactory
        self.sessionManager = sessionManager
        self.userIdentityStore = userIdentityStore
        self.tokenStore = tokenStore
        self.deviceIdentityStore = deviceIdentityStore
        self.agentStatusApplier = agentStatusApplier
        self.templateSync = templateSync
        self.shortcutSync = shortcutSync
        self.customTypeSync = customTypeSync
        connectionBroadcaster.emit(.disconnected)
    }

    nonisolated func connectionStates() -> AsyncStream<BusConnectionState> {
        connectionBroadcaster.subscribe()
    }

    /// Idempotent. Performs the initial connection attempt directly and
    /// binds subsequent starts/stops to auth-state *changes* only.
    func start() async {
        bindLifecycleIfNeeded()
        await startConnection()
    }

    private func bindLifecycleIfNeeded() {
        guard lifecycleTask == nil else { return }
        let stream = sessionManager.authStateChanges()
        lifecycleTask = Task { [weak self] in
            for await state in stream {
                guard let self else { return }
                switch state {
                case .userOnly, .fullyConnected:
                    await self.startConnection()
                case .unauthenticated:
                    await self.stopConnection()
                    // Cross-account isolation: drop cached agents/templates/
                    // shortcuts/custom types so a different account signing in
                    // the same process never sees the previous user's data.
                    await self.agentStatusApplier.clear()
                    await self.templateSync.clear()
                    await self.shortcutSync.clear()
                    await self.customTypeSync.clear()
                case .incompatibleServer:
                    await self.stopConnection()
                }
            }
        }
    }

    private func startConnection() async {
        guard core == nil else { return }
        guard userIdentityStore.isUserLoggedIn() else {
            connectionBroadcaster.emit(.disconnected)
            return
        }

        connectionBroadcaster.emit(.connecting)
        let core = webSocketFactory.make(
            Self.urlProvider(
                tokenStore: tokenStore,
                userIdentityStore: userIdentityStore,
                deviceIdentityStore: deviceIdentityStore
            ),
            { [userIdentityStore] in userIdentityStore.isUserLoggedIn() }
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
            await reconcileAfterConnect()
        case .disconnected, .error:
            connectionBroadcaster.emit(.connecting)
        case .message(let text):
            await route(text)
        case .binary:
            break
        case .tokenRejected:
            await sessionManager.invalidateUserSession(.tokenRejected)
        case .accountRemoved:
            await sessionManager.invalidateUserSession(.accountRemoved)
        case .incompatibleServer(let reason):
            await sessionManager.markIncompatibleServer(
                AuthState.IncompatibleServer(
                    reason: reason,
                    minClientVersion: 0,
                    storeUrl: AppConfig.appStoreURL,
                    storeFallbackUrl: AppConfig.appStoreFallbackURL
                )
            )
        }
    }

    private func reconcileAfterConnect() async {
        try? await agentStatusApplier.refreshAgents()
        try? await templateSync.refreshTemplates()
        try? await shortcutSync.refreshShortcuts()
        try? await customTypeSync.refreshCustomTypes()
    }

    private func route(_ text: String) async {
        switch UserEnvelopeDemux.demux(text) {
        case .agentStatusChanged(let agentId, let online):
            await agentStatusApplier.updateAgentOnline(agentId: agentId, online: online)
        case .templateUpserted(let template):
            await templateSync.applyUpserted(template)
        case .templateDeleted(let templateId):
            await templateSync.applyDeleted(templateId: templateId)
        case .shortcutUpserted(let shortcut):
            await shortcutSync.applyUpserted(shortcut)
        case .shortcutDeleted(let shortcutId):
            await shortcutSync.applyDeleted(shortcutId: shortcutId)
        case .customTypeUpserted(let type):
            await customTypeSync.applyUpserted(type)
        case .customTypeDeleted(let typeId):
            await customTypeSync.applyDeleted(typeId: typeId)
            // The server cascades the type's saved shortcuts in the same
            // transaction without per-shortcut events — mirror it locally.
            await shortcutSync.purgeKind("custom_\(typeId)")
        case .none:
            break
        }
    }

    private static func urlProvider(
        tokenStore: TokenStore,
        userIdentityStore: UserIdentityStore,
        deviceIdentityStore: DeviceIdentityStore
    ) -> @Sendable () async throws -> String? {
        {
            guard let token = userIdentityStore.getUserAccessToken() else { return nil }
            let base: String?
            switch tokenStore.getConnectionMode() {
            case .relay:
                base = tokenStore.getRelayServerUrl()
            case .direct:
                // Single-agent dev fallback: the agent host doubles as the
                // central server.
                base = tokenStore.getAgentUrl()
            }
            // User-events is a user-scoped central-server channel. Before any
            // agent is connected (userOnly — connection mode still .direct with
            // no agent URL), fall back to the central server so
            // `agent.status_changed` / `template.*` still arrive; this is what
            // makes first-pair "waiting for online" and userOnly template live
            // sync work (Android parity: the user socket follows the login).
            let effectiveBase = base ?? AppConfig.centralServerURL
            guard !effectiveBase.isEmpty else { return nil }
            // mode .direct: the user channel is never relay-path-rewritten.
            let url = RelayRouting.webSocketURLString(
                baseURL: effectiveBase,
                path: "ws/user-events",
                mode: .direct,
                token: token
            )
            // Tells the relay which device this socket delivers to, so an
            // attention push skips this device and still reaches the account's
            // others.
            return RelayRouting.appendQueryItem(
                to: url,
                name: "device_id",
                value: deviceIdentityStore.getOrCreateDeviceId()
            )
        }
    }
}

/// Demux for the user-scoped channel (Android `UserEventBus.handleMessage`).
/// Unknown types and malformed/undecodable payloads are silently ignored
/// (no `UnknownEnvelopeType` counterpart on this channel).
enum UserEnvelopeDemux {
    enum Action: Equatable, Sendable {
        case agentStatusChanged(agentId: String, online: Bool)
        case templateUpserted(TemplateDto)
        case templateDeleted(templateId: String)
        case shortcutUpserted(ShortcutDto)
        case shortcutDeleted(shortcutId: String)
        case customTypeUpserted(CustomAgentTypeDto)
        case customTypeDeleted(typeId: String)
        case none
    }

    static func demux(_ text: String) -> Action {
        guard let envelope = EventEnvelope(text: text), let data = envelope.payloadData else {
            return .none
        }
        switch envelope.type {
        case "agent.status_changed":
            guard let payload = try? WireJSON.decoder().decode(AgentStatusChangedPayload.self, from: data) else {
                return .none
            }
            return .agentStatusChanged(agentId: payload.agent_id, online: payload.online)
        case "template.created", "template.updated":
            guard let template = try? WireJSON.decoder().decode(TemplateDto.self, from: data) else {
                return .none
            }
            return .templateUpserted(template)
        case "template.deleted":
            guard let payload = try? WireJSON.decoder().decode(TemplateDeletedPayload.self, from: data) else {
                return .none
            }
            return .templateDeleted(templateId: payload.template_id)
        case "shortcut.created", "shortcut.updated":
            guard let shortcut = try? WireJSON.decoder().decode(ShortcutDto.self, from: data) else {
                return .none
            }
            return .shortcutUpserted(shortcut)
        case "shortcut.deleted":
            guard let payload = try? WireJSON.decoder().decode(ShortcutDeletedPayload.self, from: data) else {
                return .none
            }
            return .shortcutDeleted(shortcutId: payload.shortcut_id)
        case "custom_type.created", "custom_type.updated":
            guard let type = try? WireJSON.decoder().decode(CustomAgentTypeDto.self, from: data) else {
                return .none
            }
            return .customTypeUpserted(type)
        case "custom_type.deleted":
            guard let payload = try? WireJSON.decoder().decode(CustomTypeDeletedPayload.self, from: data) else {
                return .none
            }
            return .customTypeDeleted(typeId: payload.id)
        default:
            return .none
        }
    }
}
