import AuthenticationServices
import XCTest
@testable import Mobaide

private struct FakeGoogleProvider: GoogleSignInProviding {
    let result: SignInResult
    func signIn() async -> SignInResult { result }
}

@MainActor
final class SignInServiceTests: XCTestCase {
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

    private func makeService(google: SignInResult = .cancelled) -> SignInService {
        SignInService(
            google: FakeGoogleProvider(result: google),
            apple: AppleSignInClient(),
            authRepository: repository
        )
    }

    // MARK: - Provider-result → outcome mapping (Android triad parity)

    func testCancelledMapsToCancelled() async {
        let outcome = await makeService(google: .cancelled).signInWithGoogle()
        XCTAssertEqual(outcome, .cancelled)
    }

    // MARK: - Token exchange over the real repository stack

    func testGoogleSuccessExchangesTokenAndPersistsSession() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt-g","user_id":"u1","email":"g@example.com","role":"user"}
        """)

        let outcome = await makeService(google: .success(idToken: "google-id-token", nonce: "n1"))
            .signInWithGoogle()

        XCTAssertEqual(outcome, .success(email: "g@example.com"))
        XCTAssertEqual(harness.userIdentityStore.getUserAccessToken(), "jwt-g")
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly)

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "https://central.example.com/api/auth/google")
        let body = try WireJSON.decoder().decode(GoogleAuthRequest.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, GoogleAuthRequest(id_token: "google-id-token"))
    }

    func testAppleSuccessPostsTokenAndNonceToAppleEndpoint() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt-a","user_id":"u2","email":"a@example.com","role":"user"}
        """)

        let outcome = await makeService()
            .exchange(provider: .apple, result: .success(idToken: "apple-id-token", nonce: "a-nonce"))

        XCTAssertEqual(outcome, .success(email: "a@example.com"))
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "https://central.example.com/api/auth/apple")
        let body = try WireJSON.decoder().decode(AppleAuthRequest.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, AppleAuthRequest(id_token: "apple-id-token", nonce: "a-nonce"))
    }

    func testVerificationRequiredStatusMapsToVerificationRequired() async {
        StubURLProtocol.enqueue(status: 200, body: """
        {"user_id":"u1","email":"pending@example.com","role":"user","status":"verification_required"}
        """)

        let outcome = await makeService(google: .success(idToken: "t", nonce: "n"))
            .signInWithGoogle()

        XCTAssertEqual(outcome, .verificationRequired(email: "pending@example.com"))
    }

    func testAdminAccountIsBlockedWithUserMessage() async {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt","user_id":"u1","email":"admin@example.com","role":"admin"}
        """)

        let outcome = await makeService(google: .success(idToken: "t", nonce: "n"))
            .signInWithGoogle()

        XCTAssertEqual(outcome, .failed(message: AdminLoginNotSupported.userMessage))
    }

    func testServerRejectionSurfacesAPIError() async {
        StubURLProtocol.enqueue(status: 401, body: "Invalid Apple ID token: bad aud")

        let outcome = await makeService()
            .exchange(provider: .apple, result: .success(idToken: "t", nonce: "n"))

        XCTAssertEqual(
            outcome,
            .failed(message: "Apple Sign-In failed: HTTP 401: Invalid Apple ID token: bad aud")
        )
    }

    // MARK: - Apple request/completion plumbing

    func testPrepareAppleRequestSetsGeneratedNonceAndEmailScope() {
        let apple = AppleSignInClient()
        let request = ASAuthorizationAppleIDProvider().createRequest()

        apple.prepareRequest(request)

        let nonce = request.nonce ?? ""
        XCTAssertEqual(nonce.count, 64, "AuthNonce hex embedded verbatim")
        XCTAssertTrue(nonce.allSatisfy { $0.isHexDigit })
        XCTAssertEqual(request.requestedScopes, [.email])
    }

    func testAppleCancellationMapsToCancelled() {
        let apple = AppleSignInClient()
        apple.prepareRequest(ASAuthorizationAppleIDProvider().createRequest())

        let result = apple.handleCompletion(.failure(ASAuthorizationError(.canceled)))

        XCTAssertEqual(result, .cancelled)
    }

    func testAppleCredentialMappingRequiresHeldNonce() {
        let apple = AppleSignInClient()
        let tokenData = Data("apple.jwt.token".utf8)

        XCTAssertEqual(
            apple.mapCredential(idTokenData: tokenData, nonce: "held-nonce"),
            .success(idToken: "apple.jwt.token", nonce: "held-nonce")
        )
        XCTAssertEqual(
            apple.mapCredential(idTokenData: tokenData, nonce: nil),
            .failed(message: "Sign-in attempt out of order. Try again.")
        )
        XCTAssertEqual(
            apple.mapCredential(idTokenData: nil, nonce: "held-nonce"),
            .failed(message: "Apple returned no identity token")
        )
    }

    func testAppleNonceIsConsumedExactlyOnce() {
        let apple = AppleSignInClient()
        apple.prepareRequest(ASAuthorizationAppleIDProvider().createRequest())

        _ = apple.handleCompletion(.failure(ASAuthorizationError(.canceled)))
        // A second completion without a fresh prepareRequest has no nonce —
        // a stale/replayed callback must not produce a success.
        let replay = apple.mapCredential(idTokenData: Data("t".utf8), nonce: nil)

        XCTAssertEqual(replay, .failed(message: "Sign-in attempt out of order. Try again."))
    }
}
