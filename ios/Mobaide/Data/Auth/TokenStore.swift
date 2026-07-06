import Foundation

/// Mirror of Android `TokenStore` (EncryptedSharedPreferences file
/// "mobaide_auth"): agent-connection credentials plus the DIRECT/RELAY
/// connection profile currently in use.
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

    func saveAgentUrl(_ url: String) {
        keychain.set(RelayRouting.trimTrailingSlashes(url), forKey: Self.keyAgentUrl)
    }

    func getAgentUrl() -> String? { keychain.string(forKey: Self.keyAgentUrl) }

    func getConnectionMode() -> ConnectionMode {
        keychain.string(forKey: Self.keyConnectionMode) == "relay" ? .relay : .direct
    }

    func saveRelayCredentials(
        serverUrl: String,
        agentId: String,
        pairingId: String,
        deviceId: String,
        accessToken: String,
        refreshToken: String
    ) {
        keychain.set("relay", forKey: Self.keyConnectionMode)
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

    /// Base URL agent-scoped REST calls should use: the agent itself in
    /// DIRECT mode, the relay's `/proxy/{agentId}` prefix in RELAY mode.
    func getEffectiveBaseUrl() -> String? {
        RelayRouting.agentRESTBaseURL(
            mode: getConnectionMode(),
            agentURL: getAgentUrl(),
            relayServerURL: getRelayServerUrl(),
            agentId: getAgentId()
        )
    }

    /// Full agent credentials on file for the current mode. RELAY does not
    /// require pairing/device ids — they only matter for re-pairing.
    func isLoggedIn() -> Bool {
        switch getConnectionMode() {
        case .direct:
            return getAccessToken() != nil && getRefreshToken() != nil && getAgentUrl() != nil
        case .relay:
            return getAccessToken() != nil && getRefreshToken() != nil
                && getRelayServerUrl() != nil && getAgentId() != nil
        }
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
