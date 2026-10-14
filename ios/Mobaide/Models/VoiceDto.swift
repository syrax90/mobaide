import Foundation

// `state`, `host.recommendation`, `reasons[].code` and `error.code` are open
// vocabularies kept as `String`: an unknown value reads as "voice
// unavailable". Nullable keys are optionals, so a re-encode drops them, as the
// fixtures' `decoded_canonical` expects.

struct VoiceModelInfo: Codable, Equatable, Sendable {
    let id: String
    let name: String
    let languages: [String]
    let download_bytes: Int64
    let disk_bytes: Int64
    let ram_bytes: Int64
    /// Attribution and license text shown next to the model (absent from
    /// agents that predate it).
    let notice: String?
}

struct VoiceProgress: Codable, Equatable, Sendable {
    let done_bytes: Int64
    let total_bytes: Int64
}

struct VoiceHostReason: Codable, Equatable, Sendable {
    let code: String
    let message: String
}

struct VoiceHostInfo: Codable, Equatable, Sendable {
    let ram_total_bytes: Int64?
    let disk_free_bytes: Int64?
    let cpu_count: Int?
    let recommendation: String
    let reasons: [VoiceHostReason]
}

struct VoiceErrorInfo: Codable, Equatable, Sendable {
    let code: String
    let message: String
}

/// `GET /api/voice/status`, the install/cancel/remove answers and the
/// `voice.changed` payload.
struct VoiceStatusResponse: Codable, Equatable, Sendable {
    static let stateUnsupported = "unsupported"
    static let stateNotInstalled = "not_installed"
    static let stateQueued = "queued"
    static let stateDownloading = "downloading"
    static let stateVerifying = "verifying"
    static let stateReady = "ready"
    static let stateFailed = "failed"
    static let recommendationLowResources = "low_resources"

    let state: String
    let enabled: Bool
    /// Enabled with the model in place and only the runtime missing.
    let repairing: Bool
    let model: VoiceModelInfo
    let progress: VoiceProgress?
    let host: VoiceHostInfo
    let speed_factor: Double?
    let error: VoiceErrorInfo?

    init(
        state: String,
        enabled: Bool,
        repairing: Bool = false,
        model: VoiceModelInfo,
        progress: VoiceProgress? = nil,
        host: VoiceHostInfo,
        speed_factor: Double? = nil,
        error: VoiceErrorInfo? = nil
    ) {
        self.state = state
        self.enabled = enabled
        self.repairing = repairing
        self.model = model
        self.progress = progress
        self.host = host
        self.speed_factor = speed_factor
        self.error = error
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        state = try container.decode(String.self, forKey: .state)
        enabled = try container.decode(Bool.self, forKey: .enabled)
        repairing = try container.decodeIfPresent(Bool.self, forKey: .repairing) ?? false
        model = try container.decode(VoiceModelInfo.self, forKey: .model)
        progress = try container.decodeIfPresent(VoiceProgress.self, forKey: .progress)
        host = try container.decode(VoiceHostInfo.self, forKey: .host)
        speed_factor = try container.decodeIfPresent(Double.self, forKey: .speed_factor)
        error = try container.decodeIfPresent(VoiceErrorInfo.self, forKey: .error)
    }
}

struct VoiceInstallRequest: Codable, Equatable, Sendable {
    let model_id: String?

    init(model_id: String? = nil) {
        self.model_id = model_id
    }
}

struct VoiceDictationCreateRequest: Codable, Equatable, Sendable {
    let session_id: String?

    init(session_id: String? = nil) {
        self.session_id = session_id
    }
}

struct VoiceDictationResponse: Codable, Equatable, Sendable {
    let dictation_id: String
    let sample_rate: Int
    let max_seconds: Int
    let max_chunk_bytes: Int
}

/// `pcm16_b64`: standard padded base64 with no line breaks.
struct VoiceDictationAudioRequest: Codable, Equatable, Sendable {
    let seq: Int
    let pcm16_b64: String
}

struct VoiceDictationAudioResponse: Codable, Equatable, Sendable {
    let seq: Int
    let duration_ms: Int64
}

/// `last_seq`: the highest seq the agent accepted with a 200; -1 when none was.
struct VoiceDictationFinishRequest: Codable, Equatable, Sendable {
    let last_seq: Int
}

/// `state` is `done` (`text` set) or `transcribing` (call finish again).
struct VoiceDictationResultResponse: Codable, Equatable, Sendable {
    let dictation_id: String
    let state: String
    let text: String?
    let duration_ms: Int64
}
