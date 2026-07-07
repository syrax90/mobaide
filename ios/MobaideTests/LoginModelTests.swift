import XCTest
@testable import Mobaide

private struct FakeGoogleProvider: GoogleSignInProviding {
    let result: SignInResult
    func signIn() async -> SignInResult { result }
}

@MainActor
final class LoginModelTests: XCTestCase {
    private var harness: RepositoryHarness!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        harness = RepositoryHarness()
    }

    private func makeModel(google: SignInResult = .cancelled) -> LoginModel {
        LoginModel(
            authRepository: harness.repository,
            sessionManager: harness.session.sessionManager,
            signInService: SignInService(
                google: FakeGoogleProvider(result: google),
                apple: AppleSignInClient(),
                authRepository: harness.repository
            )
        )
    }

    func testBlankFieldsAreRejectedWithoutARequest() async {
        let model = makeModel()
        model.email = "user@example.com"

        await model.loginWithPassword()

        XCTAssertEqual(model.error, "Email and password are required")
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
    }

    func testPasswordSuccessEmitsSuccessAndPersistsSession() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt-1","user_id":"u1","email":"user@example.com","role":"user"}
        """)
        let model = makeModel()
        let events = StreamCollector(model.navEvents())
        model.email = "user@example.com"
        model.password = "pw"

        await model.loginWithPassword()

        let event = try await events.next()
        XCTAssertEqual(event, .success)
        XCTAssertNil(model.error)
        XCTAssertEqual(harness.session.userIdentityStore.getUserEmail(), "user@example.com")
        let state = await harness.session.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly)
    }

    func test401MapsToInvalidCredentialsMessage() async {
        StubURLProtocol.enqueue(status: 401, body: "Invalid email or password")
        let model = makeModel()
        model.email = "user@example.com"
        model.password = "wrong"

        await model.loginWithPassword()

        XCTAssertEqual(model.error, "Invalid email or password")
        XCTAssertFalse(model.isLoading)
    }

    func testVerificationRequired403RoutesToVerifyEmail() async throws {
        StubURLProtocol.enqueue(status: 403, body: """
        {"detail":{"error":"verification_required","email":"pending@example.com"}}
        """)
        let model = makeModel()
        let events = StreamCollector(model.navEvents())
        model.email = "asked@example.com"
        model.password = "pw"

        await model.loginWithPassword()

        let event = try await events.next()
        XCTAssertEqual(event, .verificationRequired(email: "pending@example.com"))
    }

    func testAdminAccountIsBlocked() async {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt","user_id":"u1","email":"admin@example.com","role":"admin"}
        """)
        let model = makeModel()
        model.email = "admin@example.com"
        model.password = "pw"

        await model.loginWithPassword()

        XCTAssertEqual(model.error, AdminLoginNotSupported.userMessage)
    }

    func testEditingAFieldClearsTheError() async {
        let model = makeModel()
        await model.loginWithPassword()
        XCTAssertNotNil(model.error)

        model.email = "typed@example.com"

        XCTAssertNil(model.error)
    }

    func testGoogleCancelClearsLoadingWithoutError() async {
        let model = makeModel(google: .cancelled)

        await model.signInWithGoogle()

        XCTAssertFalse(model.isLoading)
        XCTAssertNil(model.error)
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
    }

    func testGoogleSuccessEmitsSuccessNavEvent() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt-g","user_id":"u1","email":"g@example.com","role":"user"}
        """)
        let model = makeModel(google: .success(idToken: "tok", nonce: "n"))
        let events = StreamCollector(model.navEvents())

        await model.signInWithGoogle()

        let event = try await events.next()
        XCTAssertEqual(event, .success)
    }

    func testGoogleFailureSurfacesErrorMessage() async {
        let model = makeModel(google: .failed(message: "boom"))

        await model.signInWithGoogle()

        XCTAssertEqual(model.error, "Google Sign-In failed: boom")
    }

    func testSessionExpiryEmitsToastMessage() async throws {
        // Session must exist for invalidation to fire (idempotent guard).
        let session = SessionHarness(userLoggedIn: true)
        harness = RepositoryHarness(session: session)
        let model = makeModel()
        let messages = StreamCollector(model.messages())
        await session.sessionManager.invalidateUserSession(.tokenRejected)

        let observeTask = Task { await model.observeSessionEvents() }
        defer { observeTask.cancel() }

        let message = try await messages.next()
        XCTAssertEqual(message, "Your session has expired. Please sign in again.")
    }

    func testAccountRemovedEmitsAdminRemovalMessage() async throws {
        // Session must exist for invalidation to fire (idempotent guard).
        let session = SessionHarness(userLoggedIn: true)
        harness = RepositoryHarness(session: session)
        let model = makeModel()
        let messages = StreamCollector(model.messages())
        await session.sessionManager.invalidateUserSession(.accountRemoved)

        let observeTask = Task { await model.observeSessionEvents() }
        defer { observeTask.cancel() }

        let message = try await messages.next()
        XCTAssertEqual(
            message,
            "Your account was removed by an administrator. Sign up to start again or contact support."
        )
    }

    func testManualLogoutStaysSilent() async {
        let session = SessionHarness(userLoggedIn: true)
        harness = RepositoryHarness(session: session)
        let model = makeModel()
        let messages = StreamCollector(model.messages())
        await session.sessionManager.invalidateUserSession(.manual)

        let observeTask = Task { await model.observeSessionEvents() }
        defer { observeTask.cancel() }

        do {
            let unexpected = try await messages.next(timeout: .milliseconds(200))
            XCTFail("expected silence, got \(unexpected)")
        } catch {
            // Timeout — the manual logout produced no toast.
        }
    }
}
