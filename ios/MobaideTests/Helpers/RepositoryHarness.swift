import Foundation
@testable import Mobaide

/// Fully-wired `AuthRepository` over `StubURLProtocol` + `SessionHarness`,
/// shared by the onboarding view-model tests. Call `StubURLProtocol.reset()`
/// in `setUp` before constructing one.
struct RepositoryHarness {
    let session: SessionHarness
    let connectionStore: ConnectionStore
    let repository: AuthRepository

    init(session: SessionHarness = SessionHarness()) {
        self.session = session
        // The store the SessionManager purges, as in AppContainer.
        connectionStore = session.connectionStore
        let client = HTTPClient(
            session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self])
        )
        let centralAPI = CentralAPI(
            centralServerURL: "https://central.example.com",
            sessionInvalidator: SessionInvalidatorAdapter(sessionManager: session.sessionManager),
            client: client
        )
        repository = AuthRepository(
            centralAPI: centralAPI,
            tokenStore: session.tokenStore,
            userIdentityStore: session.userIdentityStore,
            sessionManager: session.sessionManager,
            connectionStore: connectionStore
        )
    }
}
