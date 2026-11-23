import Foundation

/// The agent's own settings (PROTOCOL.md "keep the computer awake"), pinned by
/// `tests/protocol_fixtures/dto/agent/AgentSettingsResponse.json`. `state` is
/// an open vocabulary: a value this client does not know reads as "not active".
struct KeepAwakeStatus: Codable, Equatable, Sendable {
    static let stateOff = "off"
    static let stateActive = "active"
    static let stateFailed = "failed"
    static let stateUnsupported = "unsupported"

    let enabled: Bool
    let state: String
    let message: String?
}

/// `GET /api/agent/settings`, every settings write, and the
/// `agent_settings.changed` payload.
struct AgentSettingsResponse: Codable, Equatable, Sendable {
    let keep_awake: KeepAwakeStatus
}

/// `PUT /api/agent/settings/keep_awake`; answers the full `AgentSettingsResponse`.
struct KeepAwakeSettingsRequest: Codable, Equatable, Sendable {
    let enabled: Bool
}
