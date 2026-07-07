import CryptoKit
import Foundation
import Security

/// Result contract of an interactive provider sign-in (Google or Apple),
/// consumed by the onboarding flows via `SignInService`.
enum SignInResult: Equatable, Sendable {
    /// `idToken` plus the nonce it was fetched with; both are submitted to
    /// the server together so it can verify freshness and nonce match.
    case success(idToken: String, nonce: String)
    /// User dismissed the sign-in sheet.
    case cancelled
    /// Build-time client id missing or placeholder.
    case notConfigured
    /// No provider account available after all fallbacks.
    case noAccounts(message: String)
    /// The sign-in provider itself is unavailable on this device.
    case providerUnavailable
    /// Any other sign-in failure.
    case failed(message: String)
}

protocol GoogleSignInProviding: Sendable {
    func signIn() async -> SignInResult
}

enum AuthNonce {
    /// SHA-256 of 32 CSPRNG bytes, rendered as 64-char lowercase hex
    /// (Android `GoogleAuthClient.generateNonce` parity). Both providers
    /// embed this string verbatim in the ID token they return.
    static func generateNonce() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        if SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) != errSecSuccess {
            var generator = SystemRandomNumberGenerator()
            bytes = (0..<32).map { _ in UInt8.random(in: .min ... .max, using: &generator) }
        }
        return SHA256.hash(data: Data(bytes))
            .map { String(format: "%02x", $0) }
            .joined()
    }
}
