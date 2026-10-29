import Foundation

/// Mirror of Android `ConnectionStore` (EncryptedSharedPreferences file
/// "mobaide_connections"): saved connection profiles (direct + relay). One
/// profile is "active" at a time — its credentials are loaded into
/// `TokenStore` for the rest of the app to use.
struct ConnectionStore: Sendable {
    static let service = "mobaide_connections"

    static let keyProfiles = "profiles"
    static let keyActiveId = "active_profile_id"

    struct ConnectionProfile: Codable, Equatable, Sendable {
        let id: String
        let label: String
        /// "direct" | "relay"
        let mode: String
        let agentUrl: String?
        let relayServerUrl: String?
        let agentId: String?
        let pairingId: String?
        let deviceId: String?
        let accessToken: String?
        let refreshToken: String?
        let createdAt: Int64

        init(
            id: String,
            label: String,
            mode: String,
            agentUrl: String? = nil,
            relayServerUrl: String? = nil,
            agentId: String? = nil,
            pairingId: String? = nil,
            deviceId: String? = nil,
            accessToken: String? = nil,
            refreshToken: String? = nil,
            createdAt: Int64 = Int64(Date().timeIntervalSince1970 * 1000)
        ) {
            self.id = id
            self.label = label
            self.mode = mode
            self.agentUrl = agentUrl
            self.relayServerUrl = relayServerUrl
            self.agentId = agentId
            self.pairingId = pairingId
            self.deviceId = deviceId
            self.accessToken = accessToken
            self.refreshToken = refreshToken
            self.createdAt = createdAt
        }
    }

    private let keychain: any KeychainStoring

    init(keychain: any KeychainStoring) {
        self.keychain = keychain
    }

    func listProfiles() -> [ConnectionProfile] {
        // kotlinx parity: unknown keys ignored by default; missing optional
        // keys and a missing createdAt fall back to defaults (see the custom
        // init(from:) below).
        guard let raw = keychain.data(forKey: Self.keyProfiles) else { return [] }
        return (try? WireJSON.decoder().decode([ConnectionProfile].self, from: raw)) ?? []
    }

    func getActiveProfileId() -> String? {
        keychain.string(forKey: Self.keyActiveId)
    }

    func setActiveProfileId(_ id: String) {
        keychain.set(id, forKey: Self.keyActiveId)
    }

    func saveProfile(_ profile: ConnectionProfile) {
        var profiles = listProfiles()
        if let index = profiles.firstIndex(where: { $0.id == profile.id }) {
            profiles[index] = profile
        } else {
            profiles.append(profile)
        }
        persist(profiles)
    }

    func removeProfile(id: String) {
        persist(listProfiles().filter { $0.id != id })
        if getActiveProfileId() == id {
            keychain.removeValue(forKey: Self.keyActiveId)
        }
    }

    /// Remove every saved profile. Profiles hold non-expiring relay tokens,
    /// so they must not outlive the user session that minted them.
    func clear() {
        keychain.removeAll()
    }

    /// Capture the current `TokenStore` state into a new profile and make it active.
    @discardableResult
    func captureActiveProfile(tokenStore: TokenStore, label: String) -> ConnectionProfile {
        let profile = ConnectionProfile(
            id: UUID().uuidString,
            label: label,
            mode: tokenStore.getConnectionMode() == .relay ? "relay" : "direct",
            agentUrl: tokenStore.getAgentUrl(),
            relayServerUrl: tokenStore.getRelayServerUrl(),
            agentId: tokenStore.getAgentId(),
            pairingId: tokenStore.getPairingId(),
            deviceId: tokenStore.getDeviceId(),
            accessToken: tokenStore.getAccessToken(),
            refreshToken: tokenStore.getRefreshToken()
        )
        saveProfile(profile)
        setActiveProfileId(profile.id)
        return profile
    }

    /// Load a saved profile's credentials into `TokenStore`, making it the
    /// active connection. Profiles with incomplete credentials for their
    /// mode are left inactive (Android parity: early return before
    /// `setActiveProfileId`).
    func activateProfile(id: String, tokenStore: TokenStore) {
        guard let profile = listProfiles().first(where: { $0.id == id }) else { return }

        tokenStore.clear()

        if profile.mode == "relay" {
            guard
                let serverUrl = profile.relayServerUrl,
                let agentId = profile.agentId,
                let pairingId = profile.pairingId,
                let deviceId = profile.deviceId,
                let accessToken = profile.accessToken,
                let refreshToken = profile.refreshToken
            else { return }
            tokenStore.saveRelayCredentials(
                serverUrl: serverUrl,
                agentId: agentId,
                pairingId: pairingId,
                deviceId: deviceId,
                accessToken: accessToken,
                refreshToken: refreshToken
            )
        } else {
            guard
                let agentUrl = profile.agentUrl,
                let accessToken = profile.accessToken,
                let refreshToken = profile.refreshToken
            else { return }
            tokenStore.saveAgentUrl(agentUrl)
            tokenStore.saveTokens(accessToken: accessToken, refreshToken: refreshToken)
        }

        setActiveProfileId(id)
    }

    private func persist(_ profiles: [ConnectionProfile]) {
        guard let encoded = try? WireJSON.encoder().encode(profiles) else { return }
        keychain.set(encoded, forKey: Self.keyProfiles)
    }
}

extension ConnectionStore.ConnectionProfile {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        label = try container.decode(String.self, forKey: .label)
        mode = try container.decode(String.self, forKey: .mode)
        agentUrl = try container.decodeIfPresent(String.self, forKey: .agentUrl)
        relayServerUrl = try container.decodeIfPresent(String.self, forKey: .relayServerUrl)
        agentId = try container.decodeIfPresent(String.self, forKey: .agentId)
        pairingId = try container.decodeIfPresent(String.self, forKey: .pairingId)
        deviceId = try container.decodeIfPresent(String.self, forKey: .deviceId)
        accessToken = try container.decodeIfPresent(String.self, forKey: .accessToken)
        refreshToken = try container.decodeIfPresent(String.self, forKey: .refreshToken)
        createdAt = try container.decodeIfPresent(Int64.self, forKey: .createdAt)
            ?? Int64(Date().timeIntervalSince1970 * 1000)
    }
}
