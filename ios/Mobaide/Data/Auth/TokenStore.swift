import Foundation

/// Mirror of Android `TokenStore` (EncryptedSharedPreferences file
/// "mobaide_auth"): the credentials of the agent connection currently in
/// use.
struct TokenStore: Sendable {
    static let service = "mobaide_auth"

    static let keyAccessToken = "access_token"
    static let keyRefreshToken = "refresh_token"
    static let keyAgentUrl = "agent_url"
    static let keyConnectionMode = "connection_mode"
    static let keyRelayServerUrl = "relay_server_url"
    static let keyAgentId = "agent_id"
    static let keyPairingId = "pairing_id"
    static let keyDeviceId = "device_id"

    private static let relayConnectionMode = "relay"

    /// How soon before `exp` a token counts as "expiring" (seconds).
    static let expiryLeewaySeconds: Int64 = 60

    private let keychain: any KeychainStoring
    private let now: @Sendable () -> Date

    init(keychain: any KeychainStoring, now: @escaping @Sendable () -> Date = { Date() }) {
        self.keychain = keychain
        self.now = now
    }

    func saveTokens(accessToken: String, refreshToken: String) {
        keychain.set(accessToken, forKey: Self.keyAccessToken)
        keychain.set(refreshToken, forKey: Self.keyRefreshToken)
    }

    func getAccessToken() -> String? { keychain.string(forKey: Self.keyAccessToken) }

    func getRefreshToken() -> String? { keychain.string(forKey: Self.keyRefreshToken) }

    func saveRelayCredentials(
        serverUrl: String,
        agentId: String,
        pairingId: String,
        deviceId: String,
        accessToken: String,
        refreshToken: String
    ) {
        keychain.set(Self.relayConnectionMode, forKey: Self.keyConnectionMode)
        keychain.set(RelayRouting.trimTrailingSlashes(serverUrl), forKey: Self.keyRelayServerUrl)
        keychain.set(agentId, forKey: Self.keyAgentId)
        keychain.set(pairingId, forKey: Self.keyPairingId)
        keychain.set(deviceId, forKey: Self.keyDeviceId)
        keychain.set(accessToken, forKey: Self.keyAccessToken)
        keychain.set(refreshToken, forKey: Self.keyRefreshToken)
    }

    func getRelayServerUrl() -> String? { keychain.string(forKey: Self.keyRelayServerUrl) }

    func getAgentId() -> String? { keychain.string(forKey: Self.keyAgentId) }

    func getPairingId() -> String? { keychain.string(forKey: Self.keyPairingId) }

    func getDeviceId() -> String? { keychain.string(forKey: Self.keyDeviceId) }

    /// Base URL agent-scoped REST calls should use: the relay's
    /// `/proxy/{agentId}` prefix.
    func getEffectiveBaseUrl() -> String? {
        RelayRouting.agentRESTBaseURL(
            relayServerURL: getRelayServerUrl(),
            agentId: getAgentId()
        )
    }

    /// Identity for per-agent local preference buckets (Android
    /// `agentScopeKey`).
    func getAgentScopeKey() -> String? {
        getAgentId()
    }

    /// Full agent credentials on file. Pairing/device ids are not required —
    /// they only matter for re-pairing.
    func isLoggedIn() -> Bool {
        isRelayConnection && getAccessToken() != nil && getRefreshToken() != nil
            && getRelayServerUrl() != nil && getAgentId() != nil
    }

    /// Credentials stored without a relay connection cannot reach an agent:
    /// they count as no connection, and this removes them.
    func clearCredentialsWithoutRelay() {
        guard !isRelayConnection else { return }
        let stored = [Self.keyAgentUrl, Self.keyAccessToken, Self.keyRefreshToken]
        guard stored.contains(where: { keychain.data(forKey: $0) != nil }) else { return }
        clear()
    }

    private var isRelayConnection: Bool {
        keychain.string(forKey: Self.keyConnectionMode) == Self.relayConnectionMode
    }

    func clear() {
        keychain.removeAll()
    }

    /// True when the access token expires within `expiryLeewaySeconds`.
    /// Missing token or unparseable JWT → true (forces a refresh attempt);
    /// a token without an `exp` claim never expires → false.
    func isAccessTokenExpiringSoon() -> Bool {
        guard let token = getAccessToken() else { return true }
        do {
            guard let exp = try JWT.expirationSeconds(of: token) else { return false }
            let nowSeconds = Int64(now().timeIntervalSince1970)
            return exp - nowSeconds < Self.expiryLeewaySeconds
        } catch {
            return true
        }
    }

}
