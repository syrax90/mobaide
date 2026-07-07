import Foundation

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
    let trustKeyPinning: TrustKeyPinning
    let sessionManager: SessionManager
    let agentTokenProvider: SessionAgentTokenProvider
    let userTokenProvider: SessionUserTokenProvider
    let sessionInvalidator: SessionInvalidatorAdapter
    let agentAPI: AgentAPI
    let centralAPI: CentralAPI
    let agentEventBus: AgentEventBus
    let userEventBus: UserEventBus
    let authRepository: AuthRepository
    let foregroundSessionTracker: ForegroundSessionTracker
    let router: Router
    let authStateModel: AuthStateModel

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
        let trustKeyPinning = TrustKeyPinning()

        let initialState = AuthState.initial(
            tokenStore: tokenStore,
            userIdentityStore: userIdentityStore
        )
        let sessionManager = SessionManager(
            tokenStore: tokenStore,
            userIdentityStore: userIdentityStore,
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
            tokenStore: tokenStore
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
        self.trustKeyPinning = trustKeyPinning
        self.sessionManager = sessionManager
        self.agentTokenProvider = agentTokenProvider
        self.userTokenProvider = userTokenProvider
        self.sessionInvalidator = sessionInvalidator
        self.agentAPI = agentAPI
        self.centralAPI = centralAPI
        self.agentEventBus = agentEventBus
        self.userEventBus = userEventBus
        self.authRepository = authRepository
        self.foregroundSessionTracker = ForegroundSessionTracker()
        self.router = Router()
        self.authStateModel = AuthStateModel(initial: initialState)
    }

    /// Idempotent app-launch bootstrap (Android `MainActivity.onCreate`
    /// parity: both buses started unconditionally; each self-gates on the
    /// auth state).
    func start() async {
        guard !started else { return }
        started = true
        authStateModel.bind(to: sessionManager)
        await agentEventBus.start()
        await userEventBus.start()
    }
}
