import Foundation

struct CloudVoiceLimits: Codable, Equatable, Sendable {
    let sample_rate: Int
    let max_seconds: Int
    let max_chunk_bytes: Int
}

/// The caller's audio transcribed today (UTC) against the daily limit.
struct CloudVoiceUsage: Codable, Equatable, Sendable {
    let used_seconds: Int
    let limit_seconds: Int
    let resets_at: String
}

/// Where one agent's dictations are transcribed.
enum VoiceEngine: String, Equatable, Sendable {
    case cloud
    case local
}

/// `state` and the `engines` values are open vocabularies kept as `String`: a
/// state this client does not know reads as "not offered", and an engine it
/// does not know as "no choice yet".
struct CloudVoiceStatus: Codable, Equatable, Sendable {
    static let stateOff = "off"
    static let stateReady = "ready"
    static let stateUnavailable = "unavailable"
    static let stateLimitReached = "limit_reached"

    let state: String
    let limits: CloudVoiceLimits
    let usage: CloudVoiceUsage
    /// Agent id → the caller's choice for that agent.
    let engines: [String: String]

    /// Cloud voice input exists for this account, whether or not it works right now.
    var isOffered: Bool {
        state == Self.stateReady || state == Self.stateUnavailable || state == Self.stateLimitReached
    }

    func engine(agentId: String) -> VoiceEngine? {
        engines[agentId].flatMap(VoiceEngine.init(rawValue:))
    }

    func withEngine(_ engine: VoiceEngine, agentId: String) -> CloudVoiceStatus {
        var next = engines
        next[agentId] = engine.rawValue
        return CloudVoiceStatus(state: state, limits: limits, usage: usage, engines: next)
    }
}

/// `PUT /api/voice/cloud/engines/{agent_id}`.
struct CloudVoiceEngineRequest: Codable, Equatable, Sendable {
    let engine: String
}

/// The payload of the `cloud_voice.changed` user event: the whole map.
struct CloudVoiceChangedPayload: Codable, Equatable, Sendable {
    let engines: [String: String]
}
