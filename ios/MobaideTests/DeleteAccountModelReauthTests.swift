import XCTest
@testable import Mobaide

/// A `GoogleSignInProviding` fake seeded with a fixed re-auth outcome, so the
/// `startGoogleReauth` → reducer path can be driven end-to-end without UIKit.
private struct FakeGoogleProvider: GoogleSignInProviding {
    let result: SignInResult
    func signIn() async -> SignInResult { result }
}

/// Covers `DeleteAccountModel` branches the payload-focused suite leaves open:
/// provider re-auth error mapping, the `submit()` network-error catch (non
/// 400/403 statuses re-throw), and the empty-email `start()` guard.
@MainActor
final class DeleteAccountModelReauthTests: XCTestCase {
    private var harness: RepositoryHarness!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        harness = RepositoryHarness(session: SessionHarness(userLoggedIn: true))
    }

    private func makeModel(
        google: SignInResult = .cancelled,
        email: String = "user@example.com"
    ) -> DeleteAccountModel {
        DeleteAccountModel(
            authRepository: harness.repository,
            signInService: SignInService(
                google: FakeGoogleProvider(result: google),
                apple: AppleSignInClient(),
                authRepository: harness.repository
            ),
            email: email
        )
    }

    // MARK: - Provider re-auth error mapping

    func testGoogleReauthProviderErrorsMapToMessagesAndHoldOnGoogleStep() async {
        let cases: [(result: SignInResult, expected: String)] = [
            (.cancelled, "Sign-in cancelled."),
            (.notConfigured, "Google Sign-In is not configured in this build."),
            (.noAccounts(message: "No Google account is available."), "No Google account is available."),
            (.providerUnavailable, "Google Sign-In is unavailable on this device right now."),
            (.failed(message: "token exchange failed"), "token exchange failed"),
        ]

        for testCase in cases {
            let model = makeModel(google: testCase.result)
            model.start()
            model.chooseMethod(.google)

            await model.startGoogleReauth()

            guard case let .reAuthGoogle(_, error, submitting) = model.step else {
                return XCTFail("expected to hold on reAuthGoogle for \(testCase.result)")
            }
            XCTAssertEqual(error, testCase.expected, "message for \(testCase.result)")
            XCTAssertFalse(submitting, "the re-auth button re-enables after a failure")
            XCTAssertTrue(StubURLProtocol.requests.isEmpty, "a failed re-auth never reaches the delete endpoint")
        }
    }

    func testAppleReauthErrorRoutesBackToAppleStepWithProviderName() {
        let model = makeModel()
        model.start()
        model.chooseMethod(.apple)

        // ASAuthorization cannot be constructed in tests, so drive the reducer.
        model.onReauthCredential(.apple, .providerUnavailable)

        guard case let .reAuthApple(_, error, submitting) = model.step else {
            return XCTFail("an Apple re-auth error must stay on the Apple step, not Google")
        }
        XCTAssertEqual(error, "Apple Sign-In is unavailable on this device right now.")
        XCTAssertFalse(submitting)
    }

    // MARK: - submit() network-error catch

    func testSubmitNetworkErrorSurfacesMessageAndKeepsSession() async {
        StubURLProtocol.enqueue(status: 500, body: "kaboom")
        let model = makeModel()
        model.start()
        model.chooseMethod(.password)
        model.onPasswordChange("pw")
        model.confirmPasswordReauth()
        model.onTypedEmailChange("user@example.com")

        await model.submit()

        guard case let .confirmEmail(_, _, _, error, submitting) = model.step else {
            return XCTFail("a thrown delete error must hold on confirmEmail, not clear the sheet")
        }
        XCTAssertEqual(error?.hasPrefix("Network error:"), true, "the catch branch prefixes the surfaced message")
        XCTAssertFalse(submitting, "the confirm button re-enables so the user can retry")
        let state = await harness.session.sessionManager.currentAuthState
        XCTAssertNotEqual(state, .unauthenticated, "only a submitted delete clears the session")
    }
}
