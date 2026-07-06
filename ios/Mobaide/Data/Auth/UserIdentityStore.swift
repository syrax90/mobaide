import Foundation

/// Mirror of Android `UserIdentityStore` (EncryptedSharedPreferences file
/// "mobaide_user_identity"): the central-server user identity, stored
/// separately from agent credentials so it survives agent switches.
/// No dashboard refresh token exists — a central 401 is terminal.
struct UserIdentityStore: Sendable {
    static let service = "mobaide_user_identity"

    static let keyAccessToken = "user_access_token"
    static let keyEmail = "user_email"
    static let keyUserId = "user_id"
    static let keyRole = "user_role"
    static let keyScheduledDeletionAt = "scheduled_deletion_at"

    struct NotLoggedIn: Error {}

    private let keychain: any KeychainStoring

    init(keychain: any KeychainStoring) {
        self.keychain = keychain
    }

    func saveUserSession(
        accessToken: String,
        email: String,
        userId: String,
        role: String,
        scheduledDeletionAt: String? = nil
    ) {
        keychain.set(accessToken, forKey: Self.keyAccessToken)
        keychain.set(email, forKey: Self.keyEmail)
        keychain.set(userId, forKey: Self.keyUserId)
        keychain.set(role, forKey: Self.keyRole)
        setScheduledDeletionAt(scheduledDeletionAt)
    }

    func getUserAccessToken() -> String? { keychain.string(forKey: Self.keyAccessToken) }

    func getUserEmail() -> String? { keychain.string(forKey: Self.keyEmail) }

    func getUserId() -> String? { keychain.string(forKey: Self.keyUserId) }

    func getUserRole() -> String? { keychain.string(forKey: Self.keyRole) }

    /// ISO-8601 timestamp of the scheduled account purge, or nil when no
    /// deletion is pending.
    func getScheduledDeletionAt() -> String? {
        keychain.string(forKey: Self.keyScheduledDeletionAt)
    }

    func setScheduledDeletionAt(_ value: String?) {
        if let value, !value.isEmpty {
            keychain.set(value, forKey: Self.keyScheduledDeletionAt)
        } else {
            keychain.removeValue(forKey: Self.keyScheduledDeletionAt)
        }
    }

    func isUserLoggedIn() -> Bool { getUserAccessToken() != nil }

    func requireAccessToken() throws -> String {
        guard let token = getUserAccessToken() else { throw NotLoggedIn() }
        return token
    }

    func clearUserSession() {
        keychain.removeAll()
    }
}
