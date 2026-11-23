import Foundation

/// How one agent's dictations are transcribed: by Mobaide's speech service
/// (`cloud`), by the agent's own model (`local`), or not decided yet
/// (`choose`: the mic opens the chooser).
enum VoiceRoute: Equatable, Sendable {
    case cloud, local, choose

    /// - Cloud not offered: on-computer voice, exactly as before cloud existed.
    /// - No choice yet: an agent whose own voice input is already enabled
    ///   keeps it; any other agent asks.
    static func resolve(cloud: CloudVoiceStatus?, agentId: String?, localStatus: VoiceStatusResponse?) -> VoiceRoute {
        guard let cloud, cloud.isOffered, let agentId else { return .local }
        switch cloud.engine(agentId: agentId) {
        case .cloud: return .cloud
        case .local: return .local
        case nil: return localStatus?.enabled == true ? .local : .choose
        }
    }
}

/// Who transcribes a dictation, and the limits it publishes.
struct DictationSource: Equatable, Sendable {
    let engine: VoiceEngine
    let sampleRate: Int
    let maxSeconds: Int
}

/// What decides whether a running dictation still has somewhere to go.
struct DictationTarget: Equatable, Sendable {
    let route: VoiceRoute
    let source: DictationSource?
}
