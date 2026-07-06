import XCTest
@testable import Mobaide

final class GraceBannerModelTests: XCTestCase {
    private var harness: SessionHarness!
    private var repository: AuthRepository!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        harness = SessionHarness()
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let centralAPI = CentralAPI(
            centralServerURL: "https://central.example.com",
            sessionInvalidator: SessionInvalidatorAdapter(sessionManager: harness.sessionManager),
            client: client
        )
        let agentAPI = AgentAPI(
            baseURL: { [tokenStore = harness.tokenStore] in tokenStore.getEffectiveBaseUrl() },
            tokens: SessionAgentTokenProvider(
                tokenStore: harness.tokenStore,
                sessionManager: harness.sessionManager
            ),
            client: client
        )
        repository = AuthRepository(
            agentAPI: agentAPI,
            centralAPI: centralAPI,
            tokenStore: harness.tokenStore,
            userIdentityStore: harness.userIdentityStore,
            sessionManager: harness.sessionManager,
            connectionStore: ConnectionStore(keychain: InMemoryKeychainStore())
        )
    }

    private func saveSessionWithPendingDeletion() {
        harness.userIdentityStore.saveUserSession(
            accessToken: "jwt",
            email: "a@example.com",
            userId: "u1",
            role: "user",
            scheduledDeletionAt: "2026-07-20T00:00:00Z"
        )
    }

    @MainActor
    func testConfirmCancelSuccessClearsBannerState() async {
        saveSessionWithPendingDeletion()
        let model = GraceBannerModel(authRepository: repository)
        XCTAssertEqual(model.scheduledFor, "2026-07-20T00:00:00Z")
        model.showConfirm = true
        StubURLProtocol.enqueue(status: 200, body: """
        {"status":"cancelled","scheduled_deletion_at":"2026-07-20T00:00:00Z"}
        """)

        await model.confirmCancel()

        XCTAssertNil(model.scheduledFor, "banner must disappear immediately on success")
        XCTAssertNil(model.errorMessage)
        XCTAssertFalse(model.cancelling)
        XCTAssertFalse(model.showConfirm)
    }

    @MainActor
    func testConfirmCancelFailureKeepsBannerAndReenablesRetry() async {
        saveSessionWithPendingDeletion()
        let model = GraceBannerModel(authRepository: repository)
        model.showConfirm = true
        StubURLProtocol.enqueue(status: 503, body: "down")

        await model.confirmCancel()

        XCTAssertEqual(model.scheduledFor, "2026-07-20T00:00:00Z", "deletion stays pending on failure")
        XCTAssertNotNil(model.errorMessage)
        XCTAssertFalse(model.cancelling, "button must re-enable so the user can retry")
        XCTAssertFalse(model.showConfirm)
    }
}
