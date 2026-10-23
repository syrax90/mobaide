/**
 * The client half of the voice audio format: 16-bit mono PCM, little-endian,
 * uploaded in chunks of about one second as standard padded base64 with no
 * line breaks. Android `Pcm16.kt` and iOS `Pcm16.swift` implement the same
 * rules, and `tests/protocol_fixtures/voice/pcm16_chunks.json` pins all three.
 */

/**
 * Bytes per uploaded chunk: one second of audio, capped by the agent's
 * `max_chunk_bytes` and rounded down to whole samples. `null` when the cap is
 * too small to carry a single sample.
 */
export function chunkBytes(sampleRate: number, maxChunkBytes: number): number | null {
  let bytes = Math.min(sampleRate * 2, maxChunkBytes);
  bytes -= bytes % 2;
  return bytes >= 2 ? bytes : null;
}

/**
 * Float32 capture samples to PCM16: NaN → 0, scale by 32768, clamp, then
 * truncate toward zero. Truncation has no tie rule to disagree on, unlike
 * `Math.round` (half up) and Kotlin's `round` (half even).
 */
export function quantize(input: ArrayLike<number>): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i += 1) {
    const x = input[i] ?? 0;
    if (Number.isNaN(x)) continue;
    let v = x * 32768;
    if (v > 32767) v = 32767;
    else if (v < -32768) v = -32768;
    out[i] = Math.trunc(v);
  }
  return out;
}

export function encodeLittleEndian(samples: Int16Array): Uint8Array {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < samples.length; i += 1) {
    view.setInt16(i * 2, samples[i] ?? 0, true);
  }
  return bytes;
}

/** Standard padded base64, no line breaks (what the agent's decoder requires). */
export function toBase64(bytes: Uint8Array): string {
  let binary = '';
  // Bounded slices: spreading a whole 64 KiB chunk into one call would risk
  // the engine's argument-count limit.
  for (let offset = 0; offset < bytes.length; offset += 0x2000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x2000));
  }
  return btoa(binary);
}

/**
 * Level meter position, 0…1: the block's RMS in dBFS mapped linearly from
 * -60 dB (silence) to 0 dB, so ordinary speech moves the meter visibly.
 */
export function meterLevel(samples: Int16Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (const sample of samples) sum += sample * sample;
  const rms = Math.sqrt(sum / samples.length) / 32768;
  if (rms <= 0) return 0;
  return Math.min(1, Math.max(0, (20 * Math.log10(rms) + 60) / 60));
}
