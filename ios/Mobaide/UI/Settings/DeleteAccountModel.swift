import AuthenticationServices
import Foundation
import Observation

/// Re-auth method offered on the Explain step. Password + Google mirror
/// Android; Apple is net-new for iOS (server accepts `method="apple"`).
enum ReauthMethod: Equatable, Sendable {
    case password
    case google
    case apple

    var providerName: String {
        switch self {
        case .password: return "Password"
        case .google: return "Google"
        case .apple: return "Apple"
        }
    }
}

/// The verified re-auth proof carried from the re-auth step into the
/// typed-email confirmation step, then dispatched to the delete endpoint.
enum ReauthCredential: Equatable, Sendable {
    case password(String)
    /// `method` is the wire discriminator: `"google"` or `"apple"`.
    case oauth(method: String, idToken: String, nonce: String)
}

/// Port of Android `SettingsViewModel`'s `DeleteAccountDialogState`. Scheduled
/// vs immediate is threaded as `mode` through every step (there is no separate
/// terminal state); on success the local session is cleared immediately (the
/// WS-4010 close is redundant — Android deliberately does not wait for it).
enum DeleteAccountStep: Equatable, Sendable {
    case hidden
    case explain(mode: DeletionMode)
    case reAuthPassword(mode: DeletionMode, password: String, error: String?, submitting: Bool)
    case reAuthGoogle(mode: DeletionMode, error: String?, submitting: Bool)
    case reAuthApple(mode: DeletionMode, error: String?, submitting: Bool)
    case confirmEmail(mode: DeletionMode, typedEmail: String, credential: ReauthCredential, error: String?, submitting: Bool)
}

@MainActor
@Observable
final class DeleteAccountModel {
    private(set) var step: DeleteAccountStep = .hidden
    let email: String

    private let authRepository: AuthRepository
    private let signInService: SignInService

    init(authRepository: AuthRepository, signInService: SignInService, email: String) {
        self.authRepository = authRepository
        self.signInService = signInService
        self.email = email
    }

    var isVisible: Bool {
        if case .hidden = step { return false }
        return true
    }

    /// The mode carried by the current step (defaults to scheduled when hidden).
    var mode: DeletionMode {
        switch step {
        case .hidden:
            return .scheduled
        case .explain(let mode),
             .reAuthPassword(let mode, _, _, _),
             .reAuthGoogle(let mode, _, _),
             .reAuthApple(let mode, _, _),
             .confirmEmail(let mode, _, _, _, _):
            return mode
        }
    }

    // MARK: - Explain

    /// Android `startDeleteAccount`: no-op when not signed in (no email).
    func start() {
        guard !email.isEmpty else { return }
        step = .explain(mode: .scheduled)
    }

    func dismiss() {
        step = .hidden
    }

    func setMode(_ mode: DeletionMode) {
        guard case .explain = step else { return }
        step = .explain(mode: mode)
    }

    func chooseMethod(_ method: ReauthMethod) {
        guard case .explain(let mode) = step else { return }
        switch method {
        case .password:
            step = .reAuthPassword(mode: mode, password: "", error: nil, submitting: false)
        case .google:
            step = .reAuthGoogle(mode: mode, error: nil, submitting: false)
        case .apple:
            step = .reAuthApple(mode: mode, error: nil, submitting: false)
        }
    }

    // MARK: - Password re-auth

    func onPasswordChange(_ password: String) {
        guard case .reAuthPassword(let mode, _, _, let submitting) = step else { return }
        step = .reAuthPassword(mode: mode, password: password, error: nil, submitting: submitting)
    }

    /// Android `confirmPasswordReauth`: an empty password stays on the step
    /// with an inline error; otherwise advance to the typed-email confirmation.
    func confirmPasswordReauth() {
        guard case .reAuthPassword(let mode, let password, _, _) = step else { return }
        guard !password.isEmpty else {
            step = .reAuthPassword(mode: mode, password: password, error: "Enter your password.", submitting: false)
            return
        }
        step = .confirmEmail(mode: mode, typedEmail: "", credential: .password(password), error: nil, submitting: false)
    }

    // MARK: - Provider re-auth

    /// Re-run Google sign-in to fetch a fresh `(idToken, nonce)` without logging
    /// in, then advance (or surface a provider error).
    func startGoogleReauth() async {
        guard case .reAuthGoogle(let mode, _, _) = step else { return }
        step = .reAuthGoogle(mode: mode, error: nil, submitting: true)
        let result = await signInService.reauthenticateWithGoogle()
        onReauthCredential(.google, result)
    }

    /// `SignInWithAppleButton(onRequest:)` hook.
    func prepareAppleRequest(_ request: ASAuthorizationAppleIDRequest) {
        signInService.prepareAppleRequest(request)
    }

    /// `SignInWithAppleButton(onCompletion:)` hook: map to a raw credential and
    /// advance (or surface a provider error).
    func onAppleCompletion(_ result: Result<ASAuthorization, any Error>) {
        guard case .reAuthApple(let mode, _, _) = step else { return }
        step = .reAuthApple(mode: mode, error: nil, submitting: true)
        onReauthCredential(.apple, signInService.reauthenticateWithApple(result))
    }

    /// Reducer from a provider `SignInResult` to the next step. Internal (not
    /// private) so tests drive the Google/Apple branches without UIKit. Only
    /// acts while on a matching re-auth step.
    func onReauthCredential(_ method: ReauthMethod, _ result: SignInResult) {
        let mode = self.mode
        switch step {
        case .reAuthGoogle, .reAuthApple:
            break
        default:
            return
        }
        switch result {
        case .success(let idToken, let nonce):
            step = .confirmEmail(
                mode: mode,
                typedEmail: "",
                credential: .oauth(method: method == .apple ? "apple" : "google", idToken: idToken, nonce: nonce),
                error: nil,
                submitting: false
            )
        case .cancelled:
            setReauthError(method, mode, "Sign-in cancelled.")
        case .notConfigured:
            setReauthError(method, mode, "\(method.providerName) Sign-In is not configured in this build.")
        case .noAccounts(let message):
            setReauthError(method, mode, message)
        case .providerUnavailable:
            setReauthError(method, mode, "\(method.providerName) Sign-In is unavailable on this device right now.")
        case .failed(let message):
            setReauthError(method, mode, message)
        }
    }

    private func setReauthError(_ method: ReauthMethod, _ mode: DeletionMode, _ message: String) {
        switch method {
        case .password:
            step = .reAuthPassword(mode: mode, password: "", error: message, submitting: false)
        case .google:
            step = .reAuthGoogle(mode: mode, error: message, submitting: false)
        case .apple:
            step = .reAuthApple(mode: mode, error: message, submitting: false)
        }
    }

    // MARK: - Confirm & submit

    func onTypedEmailChange(_ value: String) {
        guard case .confirmEmail(let mode, _, let credential, _, let submitting) = step else { return }
        step = .confirmEmail(mode: mode, typedEmail: value, credential: credential, error: nil, submitting: submitting)
    }

    /// Android `submitDelete`: exact case-sensitive email match gates the
    /// request; on success clear the local session immediately (do NOT wait for
    /// the WS-4010 close — `userLogout` is idempotent, so a late 4010 is a
    /// harmless no-op).
    func submit() async {
        guard case .confirmEmail(let mode, let typedEmail, let credential, _, let submitting) = step, !submitting else {
            return
        }
        guard typedEmail == email else {
            step = .confirmEmail(mode: mode, typedEmail: typedEmail, credential: credential, error: "Type your email exactly to confirm.", submitting: false)
            return
        }
        step = .confirmEmail(mode: mode, typedEmail: typedEmail, credential: credential, error: nil, submitting: true)

        let outcome: DeleteAccountResult
        do {
            outcome = try await dispatch(credential, mode: mode)
        } catch {
            failConfirm(mode, typedEmail, credential, "Network error: \(ErrorText.message(for: error))")
            return
        }

        switch outcome {
        case .submitted:
            await authRepository.userLogout()
            step = .hidden
        case .forbidden:
            failConfirm(mode, typedEmail, credential, "This account cannot be self-deleted (seed admin).")
        case .reauthFailed(let message):
            failConfirm(mode, typedEmail, credential, message)
        }
    }

    private func dispatch(_ credential: ReauthCredential, mode: DeletionMode) async throws -> DeleteAccountResult {
        switch credential {
        case .password(let password):
            return try await authRepository.deleteAccountWithPassword(password: password, mode: mode)
        case .oauth(let method, let idToken, let nonce):
            return method == "apple"
                ? try await authRepository.deleteAccountWithApple(idToken: idToken, nonce: nonce, mode: mode)
                : try await authRepository.deleteAccountWithGoogle(idToken: idToken, nonce: nonce, mode: mode)
        }
    }

    private func failConfirm(_ mode: DeletionMode, _ typedEmail: String, _ credential: ReauthCredential, _ message: String) {
        step = .confirmEmail(mode: mode, typedEmail: typedEmail, credential: credential, error: message, submitting: false)
    }
}
