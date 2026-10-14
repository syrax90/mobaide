package com.mobaide.app.data.voice

import java.util.Base64
import kotlin.math.log10
import kotlin.math.sqrt

/**
 * The client half of the voice audio format: 16-bit mono PCM, little-endian,
 * uploaded in chunks of about one second as standard padded base64 with no
 * line breaks. Web `pcm16.ts` and iOS `Pcm16.swift` implement the same rules,
 * and `tests/protocol_fixtures/voice/pcm16_chunks.json` pins all three.
 */
object Pcm16 {
    /**
     * Bytes per uploaded chunk: one second of audio, capped by the agent's
     * `max_chunk_bytes` and rounded down to whole samples; null when the cap
     * cannot carry a single sample.
     */
    fun chunkBytes(sampleRate: Int, maxChunkBytes: Int): Int? {
        var bytes = minOf(sampleRate.toLong() * 2, maxChunkBytes.toLong()).toInt()
        bytes -= bytes % 2
        return bytes.takeIf { it >= 2 }
    }

    /**
     * Float32 samples to PCM16: NaN → 0, scale by 32768, clamp, truncate
     * toward zero. `AudioRecord` already delivers PCM16.
     */
    fun quantize(input: FloatArray): ShortArray = ShortArray(input.size) { i ->
        val x = input[i]
        if (x.isNaN()) return@ShortArray 0
        (x * 32768f).coerceIn(-32768f, 32767f).toInt().toShort()
    }

    fun encodeLittleEndian(samples: ShortArray, from: Int = 0, to: Int = samples.size): ByteArray {
        val bytes = ByteArray((to - from) * 2)
        for (i in from until to) {
            val value = samples[i].toInt()
            val offset = (i - from) * 2
            bytes[offset] = (value and 0xFF).toByte()
            bytes[offset + 1] = ((value shr 8) and 0xFF).toByte()
        }
        return bytes
    }

    /** Standard padded base64 with no line breaks; `android.util.Base64.DEFAULT` would wrap. */
    fun base64(bytes: ByteArray): String = Base64.getEncoder().encodeToString(bytes)

    /**
     * Level meter position, 0…1: the block's RMS in dBFS mapped linearly from
     * -60 dB (silence) to 0 dB, so ordinary speech moves the meter visibly.
     */
    fun meterLevel(samples: ShortArray, from: Int = 0, to: Int = samples.size): Float {
        if (to <= from) return 0f
        var sum = 0.0
        for (i in from until to) {
            val s = samples[i].toDouble()
            sum += s * s
        }
        val rms = sqrt(sum / (to - from)) / 32768.0
        if (rms <= 0.0) return 0f
        return ((20 * log10(rms) + 60) / 60).coerceIn(0.0, 1.0).toFloat()
    }
}
