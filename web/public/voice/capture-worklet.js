// Mobaide voice input capture: mixes the microphone down to mono and posts
// ~100 ms Float32 batches at the device rate to the page, where
// src/voice/resampler.ts and src/voice/pcm16.ts turn them into 16 kHz PCM16.
//
// A static same-origin file on purpose: the SPA's CSP is `script-src 'self'`
// and AudioWorklet modules fall under it, so a blob: or bundled module would
// be refused. Kept free of logic worth testing; the page does the maths.
class MobaideVoiceCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.batch = new Float32Array(Math.max(128, Math.round(sampleRate / 10)));
    this.filled = 0;
    this.stopped = false;
    this.port.onmessage = (event) => {
      if (event.data === 'flush') {
        this.flush();
        this.port.postMessage({ type: 'flushed' });
      } else if (event.data === 'stop') {
        this.flush();
        this.stopped = true;
        this.port.postMessage({ type: 'flushed' });
      }
    };
  }

  process(inputs) {
    if (this.stopped) return false;
    const input = inputs[0];
    if (input === undefined || input.length === 0) return true;
    const channels = input.length;
    const frames = input[0].length;
    for (let i = 0; i < frames; i += 1) {
      let sum = 0;
      for (let c = 0; c < channels; c += 1) sum += input[c][i];
      this.batch[this.filled] = sum / channels;
      this.filled += 1;
      if (this.filled === this.batch.length) this.flush();
    }
    return true;
  }

  flush() {
    if (this.filled === 0) return;
    const samples = this.batch.slice(0, this.filled);
    this.filled = 0;
    this.port.postMessage({ type: 'samples', samples }, [samples.buffer]);
  }
}

registerProcessor('mobaide-voice-capture', MobaideVoiceCapture);
