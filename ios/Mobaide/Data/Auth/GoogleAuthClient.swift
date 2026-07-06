import CryptoKit
import Foundation
import Security

/// Result contract of an interactive Google sign-in, consumed by the
/// onboarding flows. The concrete GoogleSignIn SDK integration lands in
/// iOS-03 — this issue ships only the contract and the nonce utility.
enum GoogleAuthResult: Equatable, Sendable {
    /// `idToken` plus the nonce it was fetched with; both are submitted to
    /// the server together so it can verify freshness and nonce match.
    case success(idToken: String, nonce: String)
    /// User dismissed the sign-in sheet.
    case cancelled
    /// Build-time client id missing or placeholder.
    case notConfigured
    /// No Google account available after all fallbacks.
    case noAccounts(message: String)
    /// The sign-in provider itself is unavailable on this device.
    case providerUnavailable
    /// Any other sign-in failure.
    case failed(message: String)
}

protocol GoogleSignInProviding: Sendable {
    func signIn() async -> GoogleAuthResult
}

enum AuthNonce {
    /// SHA-256 of 32 CSPRNG bytes, rendered as 64-char lowercase hex
    /// (Android `GoogleAuthClient.generateNonce` parity).
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
