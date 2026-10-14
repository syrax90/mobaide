import Foundation

/// The client half of the voice audio format: 16-bit mono PCM, little-endian,
/// uploaded in chunks of about one second as standard padded base64 with no
/// line breaks. Android `Pcm16.kt` and web `pcm16.ts` implement the same
/// rules, and `tests/protocol_fixtures/voice/pcm16_chunks.json` pins all three.
enum Pcm16 {
    /// Bytes per uploaded chunk: one second of audio, capped by the agent's
    /// `max_chunk_bytes` and rounded down to whole samples; nil when the cap
    /// cannot carry a single sample.
    static func chunkBytes(sampleRate: Int, maxChunkBytes: Int) -> Int? {
        var bytes = min(sampleRate * 2, maxChunkBytes)
        bytes -= bytes % 2
        return bytes >= 2 ? bytes : nil
    }

    /// Float32 samples to PCM16: NaN → 0, scale by 32768, clamp, truncate
    /// toward zero. Clamping comes first because `Int16(_:)` traps on NaN and
    /// out-of-range values.
    static func quantize<C: Collection>(_ input: C) -> [Int16] where C.Element == Float {
        input.map { x in
            if x.isNaN { return 0 }
            let scaled = min(max(x * 32768, -32768), 32767)
            return Int16(scaled.rounded(.towardZero))
        }
    }

    static func encodeLittleEndian<C: Collection>(_ samples: C) -> Data where C.Element == Int16 {
        var data = Data(capacity: samples.count * 2)
        for sample in samples {
            let value = UInt16(bitPattern: sample)
            data.append(UInt8(value & 0xFF))
            data.append(UInt8(value >> 8))
        }
        return data
    }

    /// Standard padded base64; Foundation inserts no line breaks unless asked.
    static func base64(_ data: Data) -> String {
        data.base64EncodedString()
    }

    /// Level meter position, 0…1: the block's RMS in dBFS mapped linearly from
    /// -60 dB (silence) to 0 dB, so ordinary speech moves the meter visibly.
    static func meterLevel<C: Collection>(_ samples: C) -> Float where C.Element == Int16 {
        guard !samples.isEmpty else { return 0 }
        var sum = 0.0
        for sample in samples {
            let value = Double(sample)
            sum += value * value
        }
        let rms = (sum / Double(samples.count)).squareRoot() / 32768
        guard rms > 0 else { return 0 }
        return Float(min(max((20 * log10(rms) + 60) / 60, 0), 1))
    }
}
