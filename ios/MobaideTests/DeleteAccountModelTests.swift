import XCTest
@testable import Mobaide

private struct FakeGoogleProvider: GoogleSignInProviding {
    let result: SignInResult
    func signIn() async -> SignInResult { result }
}

@MainActor
final class DeleteAccountModelTests: XCTestCase {
    private var harness: RepositoryHarness!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        harness = RepositoryHarness(session: SessionHarness(userLoggedIn: true))
    }

    private func makeModel(google: SignInResult = .cancelled) -> DeleteAccountModel {
        DeleteAccountModel(
            authRepository: harness.repository,
            signInService: SignInService(
                google: FakeGoogleProvider(result: google),
                apple: AppleSignInClient(),
                authRepository: harness.repository
            ),
            email: "user@example.com"
        )
    }

    private func decodeDeleteBody() throws -> DeleteAccountRequest {
        let body = try XCTUnwrap(StubURLProtocol.bodies.last)
        return try WireJSON.decoder().decode(DeleteAccountRequest.self, from: body)
    }

    // MARK: - Per-method payloads

    func testPasswordPathBuildsPasswordRequest() async throws {
        StubURLProtocol.enqueue(status: 204, body: "")
        let model = makeModel()
        model.start()
        model.chooseMethod(.password)
        model.onPasswordChange("hunter2")
        model.confirmPasswordReauth()
        model.onTypedEmailChange("user@example.com")

        await model.submit()

        let request = try decodeDeleteBody()
        XCTAssertEqual(request.method, "password")
        XCTAssertEqual(request.mode, "scheduled")
        XCTAssertEqual(request.password, "hunter2")
        XCTAssertNil(request.id_token)
        XCTAssertNil(request.nonce)
    }

    func testGooglePathBuildsGoogleRequestAndThreadsImmediateMode() async throws {
        StubURLProtocol.enqueue(status: 204, body: "")
        let model = makeModel(google: .success(idToken: "g-token", nonce: "g-nonce"))
        model.start()
        model.setMode(.immediate)
        model.chooseMethod(.google)
        await model.startGoogleReauth()
        model.onTypedEmailChange("user@example.com")

        await model.submit()

        let request = try decodeDeleteBody()
        XCTAssertEqual(request.method, "google")
        XCTAssertEqual(request.mode, "immediate")
        XCTAssertEqual(request.id_token, "g-token")
        XCTAssertEqual(request.nonce, "g-nonce")
        XCTAssertNil(request.password)
    }

    func testApplePathBuildsAppleRequest() async throws {
        StubURLProtocol.enqueue(status: 204, body: "")
        let model = makeModel()
        model.start()
        model.chooseMethod(.apple)
        // Drive the reducer directly — ASAuthorization can't be built in tests.
        model.onReauthCredential(.apple, .success(idToken: "a-token", nonce: "a-nonce"))
        model.onTypedEmailChange("user@example.com")

        await model.submit()

        let request = try decodeDeleteBody()
        XCTAssertEqual(request.method, "apple")
        XCTAssertEqual(request.mode, "scheduled")
        XCTAssertEqual(request.id_token, "a-token")
        XCTAssertEqual(request.nonce, "a-nonce")
        XCTAssertNil(request.password)
    }

    // MARK: - Typed-email gate (exact, case-sensitive)

    func testTypedEmailMismatchBlocksSubmission() async {
        let model = makeModel()
        model.start()
        model.chooseMethod(.password)
        model.onPasswordChange("pw")
        model.confirmPasswordReauth()
        model.onTypedEmailChange("USER@example.com")

        await model.submit()

        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "no delete request fires on an email mismatch")
        guard case .confirmEmail(_, _, _, let error, let submitting) = model.step else {
            return XCTFail("expected to stay on confirmEmail")
        }
        XCTAssertEqual(error, "Type your email exactly to confirm.")
        XCTAssertFalse(submitting)
    }

    // MARK: - Outcome routing

    func testSuccessClearsLocalSessionWithoutWaitingForWs4010() async {
        StubURLProtocol.enqueue(status: 204, body: "")
        let model = makeModel()
        model.start()
        model.chooseMethod(.password)
        model.onPasswordChange("pw")
        model.confirmPasswordReauth()
        model.onTypedEmailChange("user@example.com")

        await model.submit()

        XCTAssertEqual(model.step, .hidden)
        let state = await harness.session.sessionManager.currentAuthState
        XCTAssertEqual(state, .unauthenticated)
    }

    func testForbiddenSurfacesSeedAdminMessage() async {
        StubURLProtocol.enqueue(status: 400, body: "{}")
        let model = makeModel()
        model.start()
        model.chooseMethod(.password)
        model.onPasswordChange("pw")
        model.confirmPasswordReauth()
        model.onTypedEmailChange("user@example.com")

        await model.submit()

        guard case .confirmEmail(_, _, _, let error, _) = model.step else { return XCTFail("expected confirmEmail") }
        XCTAssertEqual(error, "This account cannot be self-deleted (seed admin).")
    }

    func testReauthFailedSurfacesServerMessage() async {
        StubURLProtocol.enqueue(status: 403, body: """
        {"detail":{"error":"reauth_required","message":"Re-authenticate and try again."}}
        """)
        let model = makeModel()
        model.start()
        model.chooseMethod(.password)
        model.onPasswordChange("pw")
        model.confirmPasswordReauth()
        model.onTypedEmailChange("user@example.com")

        await model.submit()

        guard case .confirmEmail(_, _, _, let error, _) = model.step else { return XCTFail("expected confirmEmail") }
        XCTAssertEqual(error, "Re-authenticate and try again.")
    }

    // MARK: - Empty-password guard

    func testEmptyPasswordStaysOnReAuthWithError() {
        let model = makeModel()
        model.start()
        model.chooseMethod(.password)
        model.confirmPasswordReauth()

        guard case .reAuthPassword(_, _, let error, _) = model.step else { return XCTFail("expected reAuthPassword") }
        XCTAssertEqual(error, "Enter your password.")
    }
}
