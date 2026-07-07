import AuthenticationServices
import Foundation

/// Sign in with Apple provider. SwiftUI's `SignInWithAppleButton` splits the
/// flow into an onRequest/onCompletion pair, so the per-attempt nonce is held
/// between the two callbacks and consumed exactly once.
@MainActor
final class AppleSignInClient {
    private var pendingNonce: String?

    func prepareRequest(_ request: ASAuthorizationAppleIDRequest) {
        let nonce = AuthNonce.generateNonce()
        pendingNonce = nonce
        request.requestedScopes = [.email]
        // Embedded verbatim in the identity token; the server compares it
        // against the value POSTed alongside (same convention as Google).
        request.nonce = nonce
    }

    func handleCompletion(_ result: Result<ASAuthorization, any Error>) -> SignInResult {
        let nonce = pendingNonce
        pendingNonce = nil
        switch result {
        case .success(let authorization):
            let credential = authorization.credential as? ASAuthorizationAppleIDCredential
            return mapCredential(idTokenData: credential?.identityToken, nonce: nonce)
        case .failure(let error):
            return mapError(error)
        }
    }

    /// Pure mapping seam: `ASAuthorization` cannot be constructed in tests,
    /// so the success path is verified through this method.
    func mapCredential(idTokenData: Data?, nonce: String?) -> SignInResult {
        guard let nonce else {
            return .failed(message: "Sign-in attempt out of order. Try again.")
        }
        guard let idTokenData,
              let idToken = String(data: idTokenData, encoding: .utf8),
              !idToken.isEmpty
        else {
            return .failed(message: "Apple returned no identity token")
        }
        return .success(idToken: idToken, nonce: nonce)
    }

    private func mapError(_ error: any Error) -> SignInResult {
        guard let authError = error as? ASAuthorizationError else {
            return .failed(message: String(describing: error))
        }
        switch authError.code {
        case .canceled:
            return .cancelled
        case .unknown:
            // The usual cause is no iCloud account on the device.
            return .noAccounts(
                message: "Sign in with Apple is unavailable. Check that you're signed in to iCloud and try again."
            )
        default:
            return .failed(message: authError.localizedDescription)
        }
    }
}
