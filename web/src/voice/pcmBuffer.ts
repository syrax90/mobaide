import { encodeLittleEndian, toBase64 } from './pcm16';

/**
 * Every sample of one dictation, kept until the transcript arrives so a
 * failed upload can be retried without the user speaking again.
 *
 * Preallocated to the agent's `max_seconds` (120 s at 16 kHz is 3.84 MB) and
 * truncating at that cap. Chunk `seq` always covers the same samples
 * (`seq * chunkSamples` onwards), so a resend after a gap or a restart
 * re-cuts exactly what was sent before.
 */
export class PcmBuffer {
  private samples: Int16Array;
  private count = 0;
  private capacity: number;

  constructor(capacitySamples: number) {
    this.capacity = Math.max(0, Math.floor(capacitySamples));
    this.samples = new Int16Array(this.capacity);
  }

  get sampleCount(): number {
    return this.count;
  }

  get capacitySamples(): number {
    return this.capacity;
  }

  get isFull(): boolean {
    return this.count >= this.capacity;
  }

  /** Append what fits; returns how many samples were taken. */
  append(samples: Int16Array): number {
    const taken = Math.min(this.capacity - this.count, samples.length);
    if (taken <= 0) return 0;
    this.samples.set(samples.subarray(0, taken), this.count);
    this.count += taken;
    return taken;
  }

  /**
   * How many chunks can be uploaded: only full ones while recording, plus the
   * short tail once it has stopped (`final`). Never an empty chunk.
   */
  sealedChunks(chunkSamples: number, final: boolean): number {
    if (chunkSamples <= 0) return 0;
    return final
      ? Math.ceil(this.count / chunkSamples)
      : Math.floor(this.count / chunkSamples);
  }

  /** `pcm16_b64` of chunk `seq`. */
  payload(seq: number, chunkSamples: number): string {
    const start = seq * chunkSamples;
    const end = Math.min(start + chunkSamples, this.count);
    return toBase64(encodeLittleEndian(this.samples.subarray(start, Math.max(start, end))));
  }

  /** Apply a lower `max_seconds` from the agent; drops what lies beyond it. */
  shrinkCapacity(capacitySamples: number): void {
    const next = Math.max(0, Math.floor(capacitySamples));
    if (next >= this.capacity) return;
    this.capacity = next;
    this.count = Math.min(this.count, next);
  }

  release(): void {
    this.samples = new Int16Array(0);
    this.count = 0;
    this.capacity = 0;
  }
}
