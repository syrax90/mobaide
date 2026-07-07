import Foundation
import GoogleSignIn
import UIKit

/// GoogleSignIn-SDK provider (Android `GoogleAuthClient` parity). The SDK
/// presents UI and its types are not Sendable, so the whole client lives on
/// the main actor and only primitive strings leave it.
@MainActor
final class GoogleSignInClient: GoogleSignInProviding {
    private let clientID: String?
    private let serverClientID: String?
    private let reversedClientID: String?

    init(
        clientID: String? = AppConfig.googleIOSClientID,
        serverClientID: String? = AppConfig.googleServerClientID,
        reversedClientID: String? = AppConfig.googleReversedClientID
    ) {
        self.clientID = clientID
        self.serverClientID = serverClientID
        self.reversedClientID = reversedClientID
    }

    func signIn() async -> SignInResult {
        // All three values are co-required: without the reversed-client-id
        // URL scheme the OAuth redirect can't be delivered back to the app,
        // so a partially configured build must not start the flow.
        guard let clientID, Self.isClientIdConfigured(clientID),
              let serverClientID, Self.isClientIdConfigured(serverClientID),
              Self.isReversedClientIdConfigured(reversedClientID)
        else {
            return .notConfigured
        }
        guard let presenter = Self.topViewController() else {
            return .providerUnavailable
        }
        let nonce = AuthNonce.generateNonce()
        // serverClientID makes the ID token's `aud` the web client id — the
        // audience the central server verifies (Android serverClientId
        // parity).
        GIDSignIn.sharedInstance.configuration = GIDConfiguration(
            clientID: clientID,
            serverClientID: serverClientID
        )
        do {
            let result = try await GIDSignIn.sharedInstance.signIn(
                withPresenting: presenter,
                hint: nil,
                additionalScopes: nil,
                nonce: nonce
            )
            guard let idToken = result.user.idToken?.tokenString else {
                return .failed(message: "Google returned no ID token")
            }
            return .success(idToken: idToken, nonce: nonce)
        } catch let error as GIDSignInError {
            switch error.code {
            case .canceled:
                return .cancelled
            case .hasNoAuthInKeychain:
                return .noAccounts(
                    message: "No Google account found. Add a Google account in system settings and try again."
                )
            default:
                return .failed(message: error.localizedDescription)
            }
        } catch {
            return .failed(message: String(describing: error))
        }
    }

    /// Android `GoogleAuthClient.isClientIdConfigured` parity: reject blank
    /// values and template placeholders, require the Google OAuth client-id
    /// suffix.
    static func isClientIdConfigured(_ clientId: String?) -> Bool {
        guard let clientId else { return false }
        let trimmed = clientId.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty || trimmed.hasPrefix("YOUR_WEB_CLIENT_ID") {
            return false
        }
        return trimmed.hasSuffix(".apps.googleusercontent.com")
    }

    /// The reversed iOS client id doubles as the redirect URL scheme; an
    /// unset or unresolved `$(…)` placeholder means the round trip can never
    /// complete.
    static func isReversedClientIdConfigured(_ scheme: String?) -> Bool {
        guard let scheme else { return false }
        return scheme.trimmingCharacters(in: .whitespacesAndNewlines)
            .hasPrefix("com.googleusercontent.apps.")
    }

    private static func topViewController() -> UIViewController? {
        let windows = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
        let window = windows.first(where: \.isKeyWindow) ?? windows.first
        var top = window?.rootViewController
        while let presented = top?.presentedViewController {
            top = presented
        }
        return top
    }
}
