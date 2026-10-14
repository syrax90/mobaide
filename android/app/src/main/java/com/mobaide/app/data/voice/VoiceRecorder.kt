package com.mobaide.app.data.voice

/** Why recording stopped on its own; the audio so far is still transcribed. */
enum class InterruptReason { Background, Call, Device, Limit }

/**
 * Platform audio capture at the agent's rate as PCM16. [start] throws
 * [VoiceRecorderException]; [stop] is idempotent and delivers every pending
 * sample before it returns. [Sink] callbacks arrive on the capture thread.
 */
interface VoiceRecorder {
    suspend fun start(sampleRate: Int, sink: Sink)
    suspend fun stop()

    interface Sink {
        /** [samples] is reused by the recorder after the call returns. */
        fun samples(samples: ShortArray, length: Int)

        /** 0…1 */
        fun level(level: Float)
        fun interrupted(reason: InterruptReason)
    }
}

/** [code] is [VoiceCodes.MIC_PERMISSION_DENIED] or [VoiceCodes.MIC_UNAVAILABLE]. */
class VoiceRecorderException(val code: String, message: String? = null) :
    Exception(message ?: code)
