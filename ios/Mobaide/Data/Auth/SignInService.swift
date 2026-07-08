import AuthenticationServices
import Foundation

/// One shared provider sign-in flow consumed by both the Login and Register
/// screens. Android duplicated the Google handler triad
/// (`onGoogleAuthStart`/`onGoogleAuthResult`/`onGoogleIdToken`) across both
/// view models; iOS folds provider interaction, token exchange, and
/// user-facing error mapping into this single service.
@MainActor
final class SignInService {
    enum Provider: String {
        case google = "Google"
        case apple = "Apple"
    }

    enum Outcome: Equatable, Sendable {
        /// Session persisted; the auth-state change flips the root screen.
        case success(email: String)
        case verificationRequired(email: String)
        /// User dismissed the sheet — callers clear their spinner silently.
        case cancelled
        case failed(message: String)
    }

    private let google: any GoogleSignInProviding
    private let apple: AppleSignInClient
    private let authRepository: AuthRepository

    init(
        google: any GoogleSignInProviding,
        apple: AppleSignInClient,
        authRepository: AuthRepository
    ) {
        self.google = google
        self.apple = apple
        self.authRepository = authRepository
    }

    func signInWithGoogle() async -> Outcome {
        let result = await google.signIn()
        return await exchange(provider: .google, result: result)
    }

    /// `SignInWithAppleButton(onRequest:)` hook — generates and holds the
    /// per-attempt nonce.
    func prepareAppleRequest(_ request: ASAuthorizationAppleIDRequest) {
        apple.prepareRequest(request)
    }

    /// `SignInWithAppleButton(onCompletion:)` hook.
    func handleAppleCompletion(_ result: Result<ASAuthorization, any Error>) async -> Outcome {
        let mapped = apple.handleCompletion(result)
        return await exchange(provider: .apple, result: mapped)
    }

    // MARK: - Re-authentication (account deletion)

    /// Re-run Google sign-in solely to obtain a fresh `(idToken, nonce)`
    /// credential for a destructive action (account deletion). Unlike
    /// `signInWithGoogle`, this does NOT exchange the token or flip the
    /// session — the caller submits the credential to the delete endpoint.
    func reauthenticateWithGoogle() async -> SignInResult {
        await google.signIn()
    }

    /// Apple `SignInWithAppleButton(onCompletion:)` hook for re-authentication:
    /// map the authorization to a raw `(idToken, nonce)` credential without
    /// logging in. Pair with `prepareAppleRequest(_:)` on `onRequest`.
    func reauthenticateWithApple(_ result: Result<ASAuthorization, any Error>) -> SignInResult {
        apple.handleCompletion(result)
    }

    func exchange(provider: Provider, result: SignInResult) async -> Outcome {
        switch result {
        case .cancelled:
            return .cancelled
        case .notConfigured:
            return .failed(
                message: "\(provider.rawValue) Sign-In is not configured for this build. Contact the developer."
            )
        case .noAccounts(let message):
            return .failed(message: message)
        case .providerUnavailable:
            return .failed(
                message: "\(provider.rawValue) Sign-In is unavailable on this device right now. Try again later."
            )
        case .failed(let message):
            return .failed(message: "\(provider.rawValue) Sign-In failed: \(message)")
        case .success(let idToken, let nonce):
            return await exchangeToken(provider: provider, idToken: idToken, nonce: nonce)
        }
    }

    private func exchangeToken(
        provider: Provider,
        idToken: String,
        nonce: String
    ) async -> Outcome {
        do {
            let authResult: AuthResult
            switch provider {
            case .google:
                authResult = try await authRepository.loginWithGoogle(idToken: idToken)
            case .apple:
                authResult = try await authRepository.loginWithApple(idToken: idToken, nonce: nonce)
            }
            switch authResult {
            case .success(let email, _):
                return .success(email: email)
            case .verificationRequired(let email):
                return .verificationRequired(email: email)
            }
        } catch is AdminLoginNotSupported {
            return .failed(message: AdminLoginNotSupported.userMessage)
        } catch let error as APIError {
            return .failed(message: "\(provider.rawValue) Sign-In failed: \(error.description)")
        } catch {
            return .failed(message: "\(provider.rawValue) Sign-In failed: \(String(describing: error))")
        }
    }
}
