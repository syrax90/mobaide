package com.mobaide.app.data.voice

/**
 * User-facing copy keyed by `error.code`. The agent's own message can carry
 * host details (a worker traceback tail), so it is only shown behind
 * "Details". Unknown codes get the generic line.
 */
object VoiceErrorCopy {
    /** A dictation that failed; [agent] names the computer. */
    fun dictation(code: String, agent: String): String = when (code) {
        VoiceCodes.NOT_READY -> "Voice input isn't ready on $agent."
        VoiceCodes.UNSUPPORTED -> "$agent can't run voice input."
        VoiceCodes.BUSY -> "Voice input is busy on $agent. Try again in a moment."
        VoiceCodes.TRANSCRIPTION_FAILED ->
            "Voice can't run on $agent. Retry, or reinstall voice in Settings."
        VoiceCodes.TRANSCRIPTION_TIMEOUT -> "$agent is taking too long to transcribe."
        VoiceCodes.DICTATION_NOT_FOUND -> "The dictation expired on the agent."
        VoiceCodes.NETWORK -> "The connection dropped. Your recording is kept."
        VoiceCodes.MIC_PERMISSION_DENIED -> "Microphone access is blocked."
        VoiceCodes.MIC_UNAVAILABLE -> "No microphone is available."
        VoiceCodes.BAD_AUDIO, VoiceCodes.CHUNK_TOO_LARGE, VoiceCodes.SEQ_GAP,
        VoiceCodes.DICTATION_FINISHED, VoiceCodes.TOO_LONG -> "$agent couldn't read the recording."
        else -> "Voice input failed."
    }

    /**
     * Why a cleanup failed; the transcript is inserted as heard. [kind] is
     * the wire name of the session kind whose CLI ran it.
     */
    fun cleanup(code: String, agent: String, kind: String?): String {
        val name = cleanupCliName(kind)
        return when (code) {
            VoiceCodes.CLEANUP_UNAVAILABLE -> "$name isn't installed on $agent."
            VoiceCodes.CLEANUP_OUTDATED -> cleanupUpdateCommand(kind)
                ?.let { "$name on $agent is too old. Run \"$it\" there." }
                ?: "$name on $agent is too old. Update it there."
            VoiceCodes.CLEANUP_BUSY -> "Another cleanup was still running."
            VoiceCodes.CLEANUP_TIMEOUT -> "$name took too long."
            VoiceCodes.NETWORK -> "The connection dropped."
            else -> "$name reported an error."
        }
    }

    private fun cleanupCliName(kind: String?): String = when (kind) {
        "claude" -> "Claude Code"
        "codex" -> "Codex"
        "antigravity" -> "Antigravity"
        "opencode" -> "OpenCode"
        "copilot" -> "GitHub Copilot"
        "pi" -> "Pi"
        else -> "The session's AI agent"
    }

    private fun cleanupUpdateCommand(kind: String?): String? = when (kind) {
        "claude" -> "claude update"
        "codex" -> "codex update"
        "antigravity" -> "agy update"
        "opencode" -> "opencode upgrade"
        "copilot" -> "copilot update"
        "pi" -> "pi update"
        else -> null
    }

    /** Why an install ended `failed` (`VoiceStatusResponse.error.code`). */
    fun install(code: String, repairing: Boolean): String = when (code) {
        "interrupted" ->
            if (repairing) "Voice needs a small update on this computer."
            else "The voice install was interrupted."
        "network" -> "The download failed. Check the computer's internet connection."
        "http_error" -> "The download server refused the request."
        "hash_mismatch", "size_mismatch" -> "The download was corrupted."
        "disk_full" -> "The computer is out of disk space."
        "io_error", "storage_error" -> "Voice files couldn't be written on the computer."
        "selftest_failed" -> "The speech model failed its self-test on the computer."
        else -> "Voice input couldn't be installed."
    }

    /** A refused install / cancel / remove request. */
    fun action(code: String?): String = when (code) {
        VoiceCodes.UNSUPPORTED -> "This computer can't run voice input."
        "install_in_progress" -> "Another Mobaide process is changing voice input on this computer."
        "files_in_use" -> "Some voice files are still in use. Try again in a minute."
        "storage_error" -> "Voice files couldn't be written on the computer."
        else -> "The computer didn't accept the change."
    }
}
