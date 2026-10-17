import Foundation
import UIKit
import UserNotifications

/// Manual composition root (no DI framework — Android Hilt's
/// SingletonComponent becomes explicit wiring). Everything is built once
/// in dependency order; `start()` is called from the app entry point and
/// binds the observable mirror plus both event buses.
@MainActor
final class AppContainer {
    let tokenStore: TokenStore
    let userIdentityStore: UserIdentityStore
    let connectionStore: ConnectionStore
    let deviceIdentityStore: DeviceIdentityStore
    let preferencesStore: PreferencesStore
    let terminalDisplayStore: TerminalDisplayStore
    let themeStore: ThemeStore
    let trustKeyPinning: TrustKeyPinning
    let sessionManager: SessionManager
    let agentTokenProvider: SessionAgentTokenProvider
    let userTokenProvider: SessionUserTokenProvider
    let sessionInvalidator: SessionInvalidatorAdapter
    let agentAPI: AgentAPI
    let centralAPI: CentralAPI
    let agentEventBus: AgentEventBus
    let userEventBus: UserEventBus
    let agentPairedConversion: AgentPairedConversion
    let authRepository: AuthRepository
    let signInService: SignInService
    let versionGate: VersionGateRepository
    let mailComposer: any MailComposing
    let foregroundSessionTracker: ForegroundSessionTracker
    let sessionRepository: SessionRepository
    let projectsRepository: ProjectsRepository
    let pathSuggestionRepository: PathSuggestionRepository
    let autoScanRepository: AutoScanRepository
    let agentRepository: AgentRepository
    let templatesRepository: TemplatesRepository
    let shortcutsRepository: ShortcutsRepository
    let customAgentTypesRepository: CustomAgentTypesRepository
    let deviceRepository: DeviceRepository
    let terminalSessionStore: TerminalSessionStore
    let router: Router
    let authStateModel: AuthStateModel
    let notificationService: NotificationService
    let attentionCoordinator: AttentionCoordinator
    let deepLinkCoordinator: DeepLinkCoordinator
    let appLifecycleController: AppLifecycleController
    let pushRegistrationCoordinator: PushRegistrationCoordinator

    private var started = false

    init() {
        let tokenStore = TokenStore(
            keychain: SecItemKeychainStore(service: TokenStore.service)
        )
        let userIdentityStore = UserIdentityStore(
            keychain: SecItemKeychainStore(service: UserIdentityStore.service)
        )
        let connectionStore = ConnectionStore(
            keychain: SecItemKeychainStore(service: ConnectionStore.service)
        )
        let deviceIdentityStore = DeviceIdentityStore(
            keychain: SecItemKeychainStore(
                service: DeviceIdentityStore.service,
                accessibility: .afterFirstUnlockThisDeviceOnly
            )
        )
        let preferencesStore = PreferencesStore()
        let terminalDisplayStore = TerminalDisplayStore(preferences: preferencesStore)
        let themeStore = ThemeStore(preferences: preferencesStore)
        let trustKeyPinning = TrustKeyPinning()

        let initialState = AuthState.initial(
            tokenStore: tokenStore,
            userIdentityStore: userIdentityStore
        )
        let sessionManager = SessionManager(
            tokenStore: tokenStore,
            userIdentityStore: userIdentityStore,
            preferencesStore: preferencesStore,
            initialState: initialState
        )

        let agentTokenProvider = SessionAgentTokenProvider(
            tokenStore: tokenStore,
            sessionManager: sessionManager
        )
        let userTokenProvider = SessionUserTokenProvider(userIdentityStore: userIdentityStore)
        let sessionInvalidator = SessionInvalidatorAdapter(sessionManager: sessionManager)

        let agentAPI = AgentAPI(
            baseURL: { [tokenStore] in tokenStore.getEffectiveBaseUrl() },
            tokens: agentTokenProvider
        )
        let centralAPI = CentralAPI(
            centralServerURL: AppConfig.centralServerURL,
            sessionInvalidator: sessionInvalidator
        )

        let agentRepository = AgentRepository(
            centralAPI: centralAPI,
            userIdentityStore: userIdentityStore
        )
        let templatesRepository = TemplatesRepository(
            centralAPI: centralAPI,
            userIdentityStore: userIdentityStore
        )
        let shortcutsRepository = ShortcutsRepository(
            centralAPI: centralAPI,
            userIdentityStore: userIdentityStore
        )
        let customAgentTypesRepository = CustomAgentTypesRepository(
            centralAPI: centralAPI,
            userIdentityStore: userIdentityStore
        )
        let deviceRepository = DeviceRepository(
            centralAPI: centralAPI,
            userIdentityStore: userIdentityStore
        )

        let agentPairedConversion = AgentPairedConversion(
            sink: FirebaseAnalyticsSink(),
            fetchMe: { [centralAPI] token in try await centralAPI.me(userToken: token) },
            userIdentityStore: userIdentityStore,
            state: ConversionStateStore()
        )

        let webSocketFactory = WebSocketCoreFactory.live()
        let agentEventBus = AgentEventBus(
            webSocketFactory: webSocketFactory,
            sessionManager: sessionManager,
            tokenStore: tokenStore
        )
        let userEventBus = UserEventBus(
            webSocketFactory: webSocketFactory,
            sessionManager: sessionManager,
            userIdentityStore: userIdentityStore,
            tokenStore: tokenStore,
            deviceIdentityStore: deviceIdentityStore,
            agentStatusApplier: agentRepository,
            templateSync: templatesRepository,
            shortcutSync: shortcutsRepository,
            customTypeSync: customAgentTypesRepository,
            conversion: agentPairedConversion
        )

        let authRepository = AuthRepository(
            agentAPI: agentAPI,
            centralAPI: centralAPI,
            tokenStore: tokenStore,
            userIdentityStore: userIdentityStore,
            sessionManager: sessionManager,
            connectionStore: connectionStore
        )

        self.tokenStore = tokenStore
        self.userIdentityStore = userIdentityStore
        self.connectionStore = connectionStore
        self.deviceIdentityStore = deviceIdentityStore
        self.preferencesStore = preferencesStore
        self.terminalDisplayStore = terminalDisplayStore
        self.themeStore = themeStore
        self.trustKeyPinning = trustKeyPinning
        self.sessionManager = sessionManager
        self.agentTokenProvider = agentTokenProvider
        self.userTokenProvider = userTokenProvider
        self.sessionInvalidator = sessionInvalidator
        self.agentAPI = agentAPI
        self.centralAPI = centralAPI
        self.agentEventBus = agentEventBus
        self.userEventBus = userEventBus
        self.agentPairedConversion = agentPairedConversion
        self.agentRepository = agentRepository
        self.templatesRepository = templatesRepository
        self.shortcutsRepository = shortcutsRepository
        self.customAgentTypesRepository = customAgentTypesRepository
        self.deviceRepository = deviceRepository
        self.authRepository = authRepository
        self.signInService = SignInService(
            google: GoogleSignInClient(),
            apple: AppleSignInClient(),
            authRepository: authRepository
        )
        self.versionGate = VersionGateRepository(
            centralAPI: centralAPI,
            sessionManager: sessionManager
        )
        self.mailComposer = LiveMailComposer()
        let foregroundSessionTracker = ForegroundSessionTracker()
        self.foregroundSessionTracker = foregroundSessionTracker
        let terminalSessionStore = TerminalSessionStore(
            webSocketFactory: webSocketFactory,
            sessionManager: sessionManager,
            tokenStore: tokenStore,
            centralAPI: centralAPI,
            userTokenProvider: userTokenProvider,
            foregroundSessionTracker: foregroundSessionTracker,
            preferencesStore: preferencesStore,
            shortcutsRepository: shortcutsRepository
        )
        self.terminalSessionStore = terminalSessionStore
        let sessionRepository = SessionRepository(
            agentAPI: agentAPI,
            agentEventBus: agentEventBus,
            terminalSessionStore: terminalSessionStore
        )
        self.sessionRepository = sessionRepository
        // Assigned post-init because SessionRepository takes the store in its
        // own init; weak so the store → closure → repository → store loop
        // never retains.
        terminalSessionStore.sessionKindResolver = { [weak sessionRepository, agentAPI] sessionId in
            guard let sessionRepository else { return nil }
            if let known = sessionRepository.currentSessions
                .first(where: { $0.session_id == sessionId })?.kind {
                return known
            }
            // Read-only fetch on purpose: SessionRepository.listSessions()
            // publishes the REST snapshot and evicts terminal models absent
            // from it, so a stale response on this hot cold-start path could
            // disconnect a session created meanwhile.
            let list = try? await agentAPI.listSessions()
            return list?.first(where: { $0.session_id == sessionId })?.kind
        }
        self.projectsRepository = ProjectsRepository(
            agentAPI: agentAPI,
            agentEventBus: agentEventBus
        )
        self.pathSuggestionRepository = PathSuggestionRepository(agentAPI: agentAPI, tokenStore: tokenStore)
        let autoScanWsClient = AutoScanWsClient(
            webSocketFactory: webSocketFactory,
            urlProvider: AutoScanWsClient.liveURLProvider(tokenStore: tokenStore, sessionManager: sessionManager),
            isAuthenticated: AutoScanWsClient.liveIsAuthenticated(sessionManager: sessionManager)
        )
        self.autoScanRepository = AutoScanRepository(
            agentAPI: agentAPI,
            agentEventBus: agentEventBus,
            wsClient: autoScanWsClient
        )
        self.router = Router()
        self.authStateModel = AuthStateModel(initial: initialState)

        let deepLinkCoordinator = DeepLinkCoordinator(
            router: self.router,
            authStates: { sessionManager.authStates() },
            initialAuthState: initialState,
            connectedAgentId: { [tokenStore] in tokenStore.getAgentId() },
            switchAgent: { [agentRepository, authRepository, deviceIdentityStore] agentId in
                // `connectAgentViaRelay` clears the current agent's credentials
                // before it validates anything, so an id we cannot vouch for must
                // fail here rather than cost a live connection to learn nothing.
                guard let agent = try await agentRepository.resolveAgent(id: agentId) else {
                    throw APIError(code: -1, errorBody: "that agent is no longer available")
                }
                try await authRepository.connectAgentViaRelay(
                    agentId: agent.agent_id,
                    friendlyName: agent.name,
                    deviceId: deviceIdentityStore.getOrCreateDeviceId(),
                    deviceName: deviceIdentityStore.getDeviceName()
                )
            }
        )
        self.deepLinkCoordinator = deepLinkCoordinator
        let notificationService = NotificationService(
            tracker: foregroundSessionTracker,
            onTap: { [deepLinkCoordinator] sessionId, agentId in
                deepLinkCoordinator.handleTap(sessionId: sessionId, agentId: agentId)
            }
        )
        self.notificationService = notificationService
        let attentionCoordinator = AttentionCoordinator(
            eventStream: { agentEventBus.events() },
            authStates: { sessionManager.authStates() },
            presenter: notificationService,
            snapshot: { [sessionRepository = self.sessionRepository] in
                try await sessionRepository.listSessions()
            },
            seen: AttentionSeenStore(
                preferences: preferencesStore,
                agentKey: { [tokenStore] in tokenStore.getAgentId() }
            )
        )
        self.attentionCoordinator = attentionCoordinator
        // Late-bound because the coordinator takes the service as its presenter:
        // a granted prompt recovers the alerts `post` dropped while
        // authorization was undecided, whichever request won the grant.
        notificationService.setOnAuthorizationGranted { [attentionCoordinator] in
            Task { @MainActor in attentionCoordinator.authorizationGranted() }
        }
        self.appLifecycleController = AppLifecycleController(
            coordinator: attentionCoordinator,
            agentEventBus: agentEventBus,
            terminalSessionStore: terminalSessionStore
        )
        self.pushRegistrationCoordinator = PushRegistrationCoordinator(
            authStates: { sessionManager.authStates() },
            requestRegistration: {
                UIApplication.shared.registerForRemoteNotifications()
            },
            register: { [deviceRepository, deviceIdentityStore] tokenHex in
                try await deviceRepository.registerPushToken(
                    deviceId: deviceIdentityStore.getOrCreateDeviceId(),
                    token: tokenHex,
                    environment: PushEnvironment.detect()
                )
            },
            unregisterRequest: {
                [centralAPI, userIdentityStore, deviceIdentityStore] in
                guard let userToken = userIdentityStore.getUserAccessToken()
                else { return nil }
                let deviceId = deviceIdentityStore.getOrCreateDeviceId()
                return {
                    try await centralAPI.unregisterPushToken(
                        userToken: userToken, deviceId: deviceId
                    )
                }
            }
        )
        // Register the delegate in init (not start()) so a cold-start
        // notification tap, delivered during launch, is not dropped.
        UNUserNotificationCenter.current().delegate = notificationService
    }

    /// Hands the APNs token callbacks to the registration coordinator. Called
    /// from the app entry point (the adaptor-backed delegate lives there);
    /// the delegate replays a token that arrived before this wiring.
    func attachPushDelegate(_ delegate: PushAppDelegate) {
        delegate.onToken = { [pushRegistrationCoordinator] tokenHex in
            pushRegistrationCoordinator.tokenReceived(tokenHex)
        }
    }

    /// Idempotent app-launch bootstrap (Android `MainActivity.onCreate`
    /// parity: both buses started unconditionally; each self-gates on the
    /// auth state).
    func start() async {
        guard !started else { return }
        started = true
        authStateModel.bind(to: sessionManager)
        // Launch-time version gate (pre-WebSocket, Android `onResume` parity):
        // flips to the upgrade takeover when the server speaks a wire protocol
        // this build cannot. Non-blocking — a network error is a no-op.
        Task { await versionGate.probe() }
        sessionRepository.start()
        projectsRepository.start()
        autoScanRepository.start()
        attentionCoordinator.start()
        deepLinkCoordinator.start()
        pushRegistrationCoordinator.start()
        await agentEventBus.start()
        // Armed before the user socket's first connect can reconcile.
        await agentPairedConversion.armIfNeeded()
        await userEventBus.start()
    }
}
