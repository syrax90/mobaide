/**
 * Streaming conversion of the microphone's native rate (44.1 or 48 kHz in
 * practice) to the agent's rate (16 kHz).
 *
 * The AudioContext keeps the device rate: Firefox refuses to connect a
 * microphone stream to a context running at another rate, and Safari has its
 * own limits, so the context is never asked for 16 kHz. A windowed-sinc
 * low-pass removes what the lower rate cannot carry, then linear
 * interpolation picks the output samples. Both stages keep their state
 * across blocks, so block boundaries leave no clicks.
 */
export class Resampler {
  private readonly step: number;
  private readonly taps: Float32Array;
  private history: Float32Array;
  /** Position of the next output sample, in filtered input samples; -1 is `previous`. */
  private position = 0;
  private previous = 0;

  constructor(inputRate: number, outputRate: number) {
    if (!(inputRate > 0) || !(outputRate > 0)) throw new RangeError('sample rates must be positive');
    this.step = inputRate / outputRate;
    this.taps = this.step > 1 ? lowPassTaps(this.step) : Float32Array.of(1);
    this.history = new Float32Array(this.taps.length - 1);
  }

  push(input: Float32Array): Float32Array {
    if (input.length === 0) return new Float32Array(0);
    const filtered = this.filter(input);
    const length = filtered.length;
    const out: number[] = [];
    let t = this.position;
    while (t <= length - 1) {
      const index = Math.floor(t);
      const fraction = t - index;
      const a = index < 0 ? this.previous : (filtered[index] ?? 0);
      const b = index + 1 < length ? (filtered[index + 1] ?? a) : a;
      out.push(a + (b - a) * fraction);
      t += this.step;
    }
    this.position = t - length;
    this.previous = filtered[length - 1] ?? 0;
    return Float32Array.from(out);
  }

  private filter(input: Float32Array): Float32Array {
    const taps = this.taps;
    if (taps.length === 1) return input;
    const keep = this.history.length;
    const joined = new Float32Array(keep + input.length);
    joined.set(this.history, 0);
    joined.set(input, keep);
    const out = new Float32Array(input.length);
    for (let i = 0; i < input.length; i += 1) {
      let sum = 0;
      for (let k = 0; k < taps.length; k += 1) {
        sum += (taps[k] ?? 0) * (joined[i + keep - k] ?? 0);
      }
      out[i] = sum;
    }
    this.history = joined.slice(joined.length - keep);
    return out;
  }
}

/** Hamming-windowed sinc, cut at 90 % of the output Nyquist, unity DC gain. */
function lowPassTaps(step: number): Float32Array {
  const half = Math.ceil(4 * step);
  const cutoff = 0.45 / step;
  const taps = new Float32Array(2 * half + 1);
  let sum = 0;
  for (let n = -half; n <= half; n += 1) {
    const sinc = n === 0 ? 2 * cutoff : Math.sin(2 * Math.PI * cutoff * n) / (Math.PI * n);
    const window = 0.54 + 0.46 * Math.cos((Math.PI * n) / half);
    const value = sinc * window;
    taps[n + half] = value;
    sum += value;
  }
  for (let i = 0; i < taps.length; i += 1) taps[i] = (taps[i] ?? 0) / sum;
  return taps;
}
