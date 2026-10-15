import Foundation

struct AgentSummaryResponse: Codable, Equatable, Sendable {
    let agent_id: String
    let name: String
    let online: Bool
    let created_at: String
    let last_seen_at: String?
    /// The build that last connected can uninstall itself when removed.
    let supports_uninstall: Bool

    init(
        agent_id: String,
        name: String,
        online: Bool,
        created_at: String,
        last_seen_at: String?,
        supports_uninstall: Bool = false
    ) {
        self.agent_id = agent_id
        self.name = name
        self.online = online
        self.created_at = created_at
        self.last_seen_at = last_seen_at
        self.supports_uninstall = supports_uninstall
    }
}

extension AgentSummaryResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        agent_id = try container.decode(String.self, forKey: .agent_id)
        name = try container.decode(String.self, forKey: .name)
        online = try container.decode(Bool.self, forKey: .online)
        created_at = try container.decode(String.self, forKey: .created_at)
        last_seen_at = try container.decodeIfPresent(String.self, forKey: .last_seen_at)
        supports_uninstall = try container.decodeIfPresent(Bool.self, forKey: .supports_uninstall) ?? false
    }
}

struct CreateAgentRequest: Codable, Equatable, Sendable {
    let name: String
}

/// Carries the raw token returned once on agent creation.
struct CreateAgentResponse: Codable, Equatable, Sendable {
    let agent_id: String
    let name: String
    let token: String
}

/// Carries the new raw token issued after credential rotation.
struct ReRegisterAgentResponse: Codable, Equatable, Sendable {
    let agent_id: String
    let token: String
}

struct MobileTokenResponse: Codable, Equatable, Sendable {
    let access_token: String
    let refresh_token: String
    let token_type: String

    init(access_token: String, refresh_token: String, token_type: String = "bearer") {
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_type = token_type
    }
}

extension MobileTokenResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        access_token = try container.decode(String.self, forKey: .access_token)
        refresh_token = try container.decode(String.self, forKey: .refresh_token)
        token_type = try container.decodeIfPresent(String.self, forKey: .token_type) ?? "bearer"
    }
}

struct MobileTokenRequest: Codable, Equatable, Sendable {
    let mobile_device_id: String
    let mobile_device_name: String?
    let platform: String?

    init(
        mobile_device_id: String,
        mobile_device_name: String? = nil,
        platform: String? = nil
    ) {
        self.mobile_device_id = mobile_device_id
        self.mobile_device_name = mobile_device_name
        self.platform = platform
    }
}
