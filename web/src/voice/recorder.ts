import { RecorderError, type Recorder, type RecorderSink } from './dictationController';
import { meterLevel, quantize } from './pcm16';
import { Resampler } from './resampler';
import { VOICE_CODE } from './voiceApi';

const WORKLET_URL = `${import.meta.env.BASE_URL}voice/capture-worklet.js`;
const PROCESSOR_NAME = 'mobaide-voice-capture';
/** How long `stop` waits for the worklet's last partial batch. */
const FLUSH_TIMEOUT_MS = 300;

interface WorkletMessage {
  type: string;
  samples?: Float32Array;
}

function toRecorderError(error: unknown): RecorderError {
  const name = error instanceof DOMException ? error.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return new RecorderError(VOICE_CODE.micPermissionDenied);
  }
  return new RecorderError(
    VOICE_CODE.micUnavailable,
    error instanceof Error ? error.message : undefined,
  );
}

/**
 * Microphone capture for the browser: `getUserMedia` (echo cancellation,
 * noise suppression, auto gain) → the static capture worklet (mono Float32 at
 * the device rate) → `Resampler` → `quantize` → PCM16 at the agent's rate.
 *
 * The context is created before the first await so it starts inside the
 * click's user activation. A hidden tab, `pagehide`, an AudioContext the OS
 * interrupts (iOS Safari during a call) and an unplugged microphone all end
 * the recording through `interrupted`, which keeps what was captured.
 */
export class WebAudioRecorder implements Recorder {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private node: AudioWorkletNode | null = null;
  private silent: GainNode | null = null;
  private sink: RecorderSink | null = null;
  private resampler: Resampler | null = null;
  private stopRequested = false;
  private stopping: Promise<void> | null = null;
  private flushed: (() => void) | null = null;

  private readonly onVisibilityChange = () => {
    if (document.visibilityState === 'hidden') this.sink?.interrupted('background');
  };
  private readonly onPageHide = () => {
    this.sink?.interrupted('background');
  };
  private readonly onTrackEnded = () => {
    this.sink?.interrupted('device');
  };
  private readonly onContextStateChange = () => {
    // `interrupted` is WebKit's state for a context the OS took away.
    if ((this.context?.state as string | undefined) === 'interrupted') {
      this.sink?.interrupted('background');
    }
  };

  async start(sampleRate: number, sink: RecorderSink): Promise<void> {
    const mediaDevices = navigator.mediaDevices as MediaDevices | undefined;
    if (
      mediaDevices === undefined ||
      typeof AudioContext === 'undefined' ||
      typeof AudioWorkletNode === 'undefined'
    ) {
      throw new RecorderError(VOICE_CODE.micUnavailable, 'This browser cannot record audio.');
    }
    const context = new AudioContext();
    this.context = context;
    // Listen from the click on: a tab hidden while the microphone is still
    // opening must end the start too, or the mic would go live in the background.
    this.sink = sink;
    document.addEventListener('visibilitychange', this.onVisibilityChange);
    window.addEventListener('pagehide', this.onPageHide);
    try {
      let stream: MediaStream;
      try {
        stream = await mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
      } catch (error) {
        throw toRecorderError(error);
      }
      this.stream = stream;
      if (this.stopRequested) return;
      await context.audioWorklet.addModule(WORKLET_URL);
      if (this.stopRequested) return;
      if (context.state === 'suspended') await context.resume();
      if (this.stopRequested) return;

      const source = context.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(context, PROCESSOR_NAME, {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      // A muted path to the destination keeps every engine pulling the graph;
      // a node with no route out may never be processed.
      const silent = context.createGain();
      silent.gain.value = 0;
      source.connect(node);
      node.connect(silent);
      silent.connect(context.destination);
      this.source = source;
      this.node = node;
      this.silent = silent;
      this.resampler = new Resampler(context.sampleRate, sampleRate);
      node.port.onmessage = (event: MessageEvent<WorkletMessage>) => {
        this.onWorkletMessage(event.data);
      };
      context.addEventListener('statechange', this.onContextStateChange);
      for (const track of stream.getAudioTracks()) {
        track.addEventListener('ended', this.onTrackEnded);
      }
    } catch (error) {
      this.teardown();
      throw error instanceof RecorderError ? error : toRecorderError(error);
    } finally {
      if (this.stopRequested && this.node === null) this.teardown();
    }
  }

  stop(): Promise<void> {
    this.stopRequested = true;
    this.stopping ??= this.drain().finally(() => {
      this.teardown();
    });
    return this.stopping;
  }

  private async drain(): Promise<void> {
    const node = this.node;
    if (node === null) return;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, FLUSH_TIMEOUT_MS);
      this.flushed = () => {
        clearTimeout(timer);
        resolve();
      };
      node.port.postMessage('stop');
    });
  }

  private onWorkletMessage(message: WorkletMessage): void {
    if (message.type === 'flushed') {
      const flushed = this.flushed;
      this.flushed = null;
      flushed?.();
      return;
    }
    if (message.type !== 'samples' || message.samples === undefined) return;
    const resampler = this.resampler;
    const sink = this.sink;
    if (resampler === null || sink === null) return;
    const pcm = quantize(resampler.push(message.samples));
    if (pcm.length === 0) return;
    sink.samples(pcm);
    sink.level(meterLevel(pcm));
  }

  private teardown(): void {
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
    window.removeEventListener('pagehide', this.onPageHide);
    this.context?.removeEventListener('statechange', this.onContextStateChange);
    if (this.node !== null) this.node.port.onmessage = null;
    this.source?.disconnect();
    this.node?.disconnect();
    this.silent?.disconnect();
    for (const track of this.stream?.getTracks() ?? []) {
      track.removeEventListener('ended', this.onTrackEnded);
      track.stop();
    }
    const context = this.context;
    if (context !== null && context.state !== 'closed') void context.close().catch(() => undefined);
    this.context = null;
    this.stream = null;
    this.source = null;
    this.node = null;
    this.silent = null;
    this.sink = null;
    this.resampler = null;
  }
}
