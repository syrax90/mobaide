import XCTest
@testable import Mobaide

private struct FakeGoogleProvider: GoogleSignInProviding {
    let result: SignInResult
    func signIn() async -> SignInResult { result }
}

@MainActor
final class RegisterModelTests: XCTestCase {
    private var harness: RepositoryHarness!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        harness = RepositoryHarness()
    }

    private func makeModel(google: SignInResult = .cancelled) -> RegisterModel {
        RegisterModel(
            authRepository: harness.repository,
            signInService: SignInService(
                google: FakeGoogleProvider(result: google),
                apple: AppleSignInClient(),
                authRepository: harness.repository
            )
        )
    }

    func testBlankFieldsAreRejected() async {
        let model = makeModel()

        await model.register()

        XCTAssertEqual(model.error, "Email and password are required")
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
    }

    func testPasswordMismatchIsRejected() async {
        let model = makeModel()
        model.email = "user@example.com"
        model.password = "password-1"
        model.confirmPassword = "password-2"

        await model.register()

        XCTAssertEqual(model.error, "Passwords do not match")
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
    }

    func testPasswordShorterThanEightIsRejectedWithoutARequest() async {
        let model = makeModel()
        model.email = "user@example.com"
        model.password = "1234567"
        model.confirmPassword = "1234567"

        await model.register()

        XCTAssertEqual(model.error, "Password must be at least 8 characters")
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
    }

    func testShortPasswordIsReportedBeforeAMismatch() async {
        let model = makeModel()
        model.email = "user@example.com"
        model.password = "short"
        model.confirmPassword = "other"

        await model.register()

        XCTAssertEqual(model.error, PasswordRules.tooShortMessage)
    }

    func testPasswordLengthCountsScalarsLikeTheServer() {
        // Four emoji are four scalars on the server: too short.
        XCTAssertEqual(PasswordRules.lengthError("😀😀😀😀"), PasswordRules.tooShortMessage)
        XCTAssertNil(PasswordRules.lengthError(String(repeating: "😀", count: 8)))
        // A family emoji is one grapheme but seven scalars.
        XCTAssertNil(PasswordRules.lengthError("👨‍👩‍👧‍👦a"))
    }

    func testPasswordLengthEnforcesBothBounds() {
        XCTAssertNil(PasswordRules.lengthError(String(repeating: "a", count: 128)))
        XCTAssertEqual(
            PasswordRules.lengthError(String(repeating: "a", count: 129)),
            PasswordRules.tooLongMessage
        )
    }

    func testPasswordOfExactlyEightIsSent() async {
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":"Email already registered"}"#)
        let model = makeModel()
        model.email = "user@example.com"
        model.password = "12345678"
        model.confirmPassword = "12345678"

        await model.register()

        XCTAssertEqual(StubURLProtocol.requests.count, 1)
    }

    func testConflict409MapsToAlreadyExists() async {
        StubURLProtocol.enqueue(status: 409, body: #"{"detail":"Email already registered"}"#)
        let model = makeModel()
        model.email = "taken@example.com"
        model.password = "password-1"
        model.confirmPassword = "password-1"

        await model.register()

        XCTAssertEqual(model.error, "An account with this email already exists")
    }

    func testInvalidEmail422MapsToSingleEmailCopy() async {
        StubURLProtocol.enqueue(status: 422, body: #"{"detail":[{"msg":"Value error, Enter a single valid email address"}]}"#)
        let model = makeModel()
        model.email = "a@example.com, b@example.com"
        model.password = "password-1"
        model.confirmPassword = "password-1"

        await model.register()

        XCTAssertEqual(model.error, "Enter a single valid email address")
        XCTAssertFalse(model.isLoading)
    }

    func testRateLimited429MapsToTooManyAttemptsCopy() async {
        StubURLProtocol.enqueue(
            status: 429,
            body: #"{"detail":{"error":"resend_rate_limited","cooldown_seconds_remaining":1800}}"#
        )
        let model = makeModel()
        model.email = "new@example.com"
        model.password = "password-1"
        model.confirmPassword = "password-1"

        await model.register()

        XCTAssertEqual(model.error, "Too many sign-up attempts. Wait a few minutes and try again.")
        XCTAssertFalse(model.isLoading)
    }

    func testVerificationRequiredRoutesToVerifyEmail() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"user_id":"u1","email":"new@example.com","role":"user","status":"verification_required"}
        """)
        let model = makeModel()
        let events = StreamCollector(model.navEvents())
        model.email = "new@example.com"
        model.password = "password-1"
        model.confirmPassword = "password-1"

        await model.register()

        let event = try await events.next()
        XCTAssertEqual(event, .verificationRequired(email: "new@example.com"))
        XCTAssertFalse(harness.session.userIdentityStore.isUserLoggedIn())
    }

    func testVerifiedRegistrationLogsStraightIn() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt-r","user_id":"u1","email":"new@example.com","role":"user"}
        """)
        let model = makeModel()
        let events = StreamCollector(model.navEvents())
        model.email = "new@example.com"
        model.password = "password-1"
        model.confirmPassword = "password-1"

        await model.register()

        let event = try await events.next()
        XCTAssertEqual(event, .success)
        XCTAssertEqual(harness.session.userIdentityStore.getUserAccessToken(), "jwt-r")
    }

    func testGoogleHandlerIsSharedWithLoginFlow() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt-g","user_id":"u1","email":"g@example.com","role":"user"}
        """)
        let model = makeModel(google: .success(idToken: "tok", nonce: "n"))
        let events = StreamCollector(model.navEvents())

        await model.signInWithGoogle()

        let event = try await events.next()
        XCTAssertEqual(event, .success)
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "https://central.example.com/api/auth/google")
    }

    func testGoogleCancelClearsLoadingSilently() async {
        let model = makeModel(google: .cancelled)

        await model.signInWithGoogle()

        XCTAssertFalse(model.isLoading)
        XCTAssertNil(model.error)
    }
}
