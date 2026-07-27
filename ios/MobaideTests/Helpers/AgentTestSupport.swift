import Foundation
import XCTest
@testable import Mobaide

/// Fully-wired agent/templates/devices stack over `StubURLProtocol` +
/// `SessionHarness`, plus an isolated `TrustKeyPinning` and `DeviceIdentityStore`.
/// Call `StubURLProtocol.reset()` in `setUp` before constructing one.
@MainActor
struct AgentHarness {
    let session: SessionHarness
    let centralAPI: CentralAPI
    let connectionStore: ConnectionStore
    let agentRepository: AgentRepository
    let templatesRepository: TemplatesRepository
    let shortcutsRepository: ShortcutsRepository
    let deviceRepository: DeviceRepository
    let authRepository: AuthRepository
    let deviceIdentityStore: DeviceIdentityStore
    let trustKeyPinning: TrustKeyPinning
    let router: Router

    init(session: SessionHarness = SessionHarness(userLoggedIn: true)) {
        self.session = session
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let centralAPI = CentralAPI(
            centralServerURL: "https://central.example.com",
            sessionInvalidator: SessionInvalidatorAdapter(sessionManager: session.sessionManager),
            client: client
        )
        self.centralAPI = centralAPI
        let connectionStore = ConnectionStore(keychain: InMemoryKeychainStore())
        self.connectionStore = connectionStore
        agentRepository = AgentRepository(centralAPI: centralAPI, userIdentityStore: session.userIdentityStore)
        templatesRepository = TemplatesRepository(centralAPI: centralAPI, userIdentityStore: session.userIdentityStore)
        shortcutsRepository = ShortcutsRepository(centralAPI: centralAPI, userIdentityStore: session.userIdentityStore)
        deviceRepository = DeviceRepository(centralAPI: centralAPI, userIdentityStore: session.userIdentityStore)
        let agentAPI = AgentAPI(
            baseURL: { [tokenStore = session.tokenStore] in tokenStore.getEffectiveBaseUrl() },
            tokens: SessionAgentTokenProvider(
                tokenStore: session.tokenStore,
                sessionManager: session.sessionManager
            ),
            client: client
        )
        authRepository = AuthRepository(
            agentAPI: agentAPI,
            centralAPI: centralAPI,
            tokenStore: session.tokenStore,
            userIdentityStore: session.userIdentityStore,
            sessionManager: session.sessionManager,
            connectionStore: connectionStore
        )
        deviceIdentityStore = DeviceIdentityStore(keychain: InMemoryKeychainStore())
        trustKeyPinning = TrustKeyPinning(defaults: UserDefaults(suiteName: "agent-test-\(UUID().uuidString)")!)
        router = Router()
    }

    func makeAgentListModel(
        sleeper: any Sleeper = ManualSleeper(),
        now: @escaping @Sendable () -> Date = { Date(timeIntervalSince1970: 1000) }
    ) -> AgentListModel {
        AgentListModel(
            agentRepository: agentRepository,
            authRepository: authRepository,
            trustKeyPinning: trustKeyPinning,
            deviceIdentityStore: deviceIdentityStore,
            tokenStore: session.tokenStore,
            router: router,
            sleeper: sleeper,
            now: now
        )
    }
}

/// Small TemplateDto builder for repository/model tests.
func makeTemplate(
    id: String,
    name: String,
    body: String = "echo hi",
    favorite: Bool = false,
    version: Int = 1
) -> TemplateDto {
    TemplateDto(
        template_id: id,
        name: name,
        body: body,
        is_favorite: favorite,
        last_used_at: nil,
        created_at: "t",
        updated_at: "t",
        version: version
    )
}

extension XCTestCase {
    /// Poll a condition on the main actor until it holds or the timeout lapses.
    /// Used to await async model work driven through real (stubbed) URLSession.
    @MainActor
    func waitUntil(timeout: Duration = .seconds(3), _ condition: () -> Bool) async {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: timeout)
        while !condition() {
            if clock.now >= deadline { return }
            try? await Task.sleep(for: .milliseconds(2))
        }
    }
}
