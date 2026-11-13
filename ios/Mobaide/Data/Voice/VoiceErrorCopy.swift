import Foundation

/// User-facing copy keyed by `error.code`. The agent's own message can carry
/// host details (a worker traceback tail), so it is only shown behind
/// "Details". Unknown codes get the generic line.
enum VoiceErrorCopy {
    /// A dictation that failed; `agent` names the computer.
    static func dictation(_ code: String, agent: String) -> String {
        switch code {
        case VoiceCodes.notReady: return "Voice input isn't ready on \(agent)."
        case VoiceCodes.unsupported: return "\(agent) can't run voice input."
        case VoiceCodes.busy: return "Voice input is busy on \(agent). Try again in a moment."
        case VoiceCodes.transcriptionFailed:
            return "Voice can't run on \(agent). Retry, or reinstall voice in Settings."
        case VoiceCodes.transcriptionTimeout: return "\(agent) is taking too long to transcribe."
        case VoiceCodes.dictationNotFound: return "The dictation expired on the agent."
        case VoiceCodes.network: return "The connection dropped. Your recording is kept."
        case VoiceCodes.micPermissionDenied: return "Microphone access is off for Mobaide."
        case VoiceCodes.micUnavailable: return "No microphone is available."
        case VoiceCodes.badAudio, VoiceCodes.chunkTooLarge, VoiceCodes.seqGap,
             VoiceCodes.dictationFinished, VoiceCodes.tooLong:
            return "\(agent) couldn't read the recording."
        default: return "Voice input failed."
        }
    }

    /// Why a cleanup failed; the transcript is inserted as heard. `kind` is
    /// the session kind the cleanup ran for, whose CLI the copy names.
    static func cleanup(_ code: String, agent: String, kind: String?) -> String {
        let (name, updateCommand) = cleanupCli(kind)
        switch code {
        case VoiceCodes.cleanupUnavailable: return "\(name) isn't installed on \(agent)."
        case VoiceCodes.cleanupOutdated:
            guard let updateCommand else { return "\(name) on \(agent) is too old. Update it there." }
            return "\(name) on \(agent) is too old. Run \"\(updateCommand)\" there."
        case VoiceCodes.cleanupBusy: return "Another cleanup was still running."
        case VoiceCodes.cleanupTimeout: return "\(name) took too long."
        case VoiceCodes.network: return "The connection dropped."
        default: return "\(name) reported an error."
        }
    }

    private static func cleanupCli(_ kind: String?) -> (name: String, updateCommand: String?) {
        switch kind {
        case "claude": return ("Claude Code", "claude update")
        case "codex": return ("Codex", "codex update")
        case "antigravity": return ("Antigravity", "agy update")
        case "opencode": return ("OpenCode", "opencode upgrade")
        case "copilot": return ("GitHub Copilot", "copilot update")
        case "cursor": return ("Cursor", "cursor-agent update")
        default: return ("The session's AI agent", nil)
        }
    }

    /// Why an install ended `failed` (`VoiceStatusResponse.error.code`).
    static func install(_ code: String, repairing: Bool) -> String {
        switch code {
        case "interrupted":
            return repairing ? "Voice needs a small update on this computer." : "The voice install was interrupted."
        case "network": return "The download failed. Check the computer's internet connection."
        case "http_error": return "The download server refused the request."
        case "hash_mismatch", "size_mismatch": return "The download was corrupted."
        case "disk_full": return "The computer is out of disk space."
        case "io_error", "storage_error": return "Voice files couldn't be written on the computer."
        case "selftest_failed": return "The speech model failed its self-test on the computer."
        default: return "Voice input couldn't be installed."
        }
    }

    /// A refused install / cancel / remove request.
    static func action(_ code: String?) -> String {
        switch code {
        case VoiceCodes.unsupported: return "This computer can't run voice input."
        case "install_in_progress": return "Another Mobaide process is changing voice input on this computer."
        case "files_in_use": return "Some voice files are still in use. Try again in a minute."
        case "storage_error": return "Voice files couldn't be written on the computer."
        default: return "The computer didn't accept the change."
        }
    }
}
