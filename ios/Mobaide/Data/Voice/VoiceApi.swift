import Foundation

struct VoiceDictationOpened: Equatable, Sendable {
    let dictationId: String
    let sampleRate: Int
    let maxSeconds: Int
    let maxChunkBytes: Int
}

/// `state` is `done` (`text` set) or `transcribing` (ask again); an open vocabulary.
struct VoiceFinishResult: Equatable, Sendable {
    let state: String
    let text: String?
}

/// The dictation half of the agent's voice API, as the uploader sees it.
protocol VoiceApi: Sendable {
    func create() async throws -> VoiceDictationOpened
    func audio(dictationId: String, seq: Int, pcm16B64: String) async throws
    func finish(dictationId: String, lastSeq: Int) async throws -> VoiceFinishResult
    /// Delete the dictation in the background and ignore every failure: the
    /// route is idempotent, and an older relay drops the connection after its
    /// 204, so even a "failure" usually means it worked.
    func discard(dictationId: String)
}

/// A refused or failed voice request. `code` is the agent's `detail.code` (an
/// open vocabulary); `network` marks a request that never got an answer.
struct VoiceApiError: Error, Equatable, Sendable {
    let status: Int?
    let code: String?
    let message: String
    var detailMessage: String?
    var expectedSeq: Int?
    var network = false

    /// Voice refusals carry `{"detail": {"code", "message", …}}` on any
    /// status; `expected_seq` rides along with `seq_gap`.
    static func map(_ error: any Error) -> any Error {
        if error is VoiceApiError || error is CancellationError { return error }
        if (error as? URLError)?.code == .cancelled { return CancellationError() }
        if let apiError = error as? APIError {
            var code: String?
            var message: String?
            var expected: Int?
            if let data = apiError.errorBody.data(using: .utf8),
               let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = root["detail"] as? [String: Any] {
                code = detail["code"] as? String
                message = detail["message"] as? String
                expected = detail["expected_seq"] as? Int
            }
            return VoiceApiError(
                status: apiError.code,
                code: code,
                message: "HTTP \(apiError.code)",
                detailMessage: message,
                expectedSeq: expected
            )
        }
        if error is URLError {
            return VoiceApiError(status: nil, code: nil, message: error.localizedDescription, network: true)
        }
        return VoiceApiError(status: nil, code: nil, message: error.localizedDescription)
    }
}

enum VoiceCodes {
    static let notReady = "voice_not_ready"
    static let busy = "voice_busy"
    static let unsupported = "voice_unsupported"
    static let dictationNotFound = "dictation_not_found"
    static let seqGap = "seq_gap"
    static let dictationFinished = "dictation_finished"
    static let tooLong = "too_long"
    static let chunkTooLarge = "chunk_too_large"
    static let badAudio = "bad_audio"
    static let transcriptionFailed = "transcription_failed"
    static let cleanupUnavailable = "cleanup_unavailable"
    static let cleanupOutdated = "cleanup_outdated"
    static let cleanupBusy = "cleanup_busy"
    /// Also the client's own code when the agent's answer never arrives.
    static let cleanupTimeout = "cleanup_timeout"
    static let cleanupFailed = "cleanup_failed"
    // Client-side codes, never sent by the agent.
    static let network = "network"
    static let transcriptionTimeout = "transcription_timeout"
    static let micPermissionDenied = "mic_permission_denied"
    static let micUnavailable = "mic_unavailable"
    static let generic = "generic"
}
