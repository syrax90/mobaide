import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { InterruptReason, RecorderSink } from './dictationController';
import { WebAudioRecorder } from './recorder';

class FakeTrack {
  stopped = false;

  addEventListener(): void {}

  removeEventListener(): void {}

  stop(): void {
    this.stopped = true;
  }
}

class FakeStream {
  readonly track = new FakeTrack();

  getAudioTracks(): FakeTrack[] {
    return [this.track];
  }

  getTracks(): FakeTrack[] {
    return [this.track];
  }
}

/** jsdom has no Web Audio; only what a start that never builds the graph touches. */
class FakeAudioContext {
  static created: FakeAudioContext[] = [];
  state: 'running' | 'closed' = 'running';
  readonly modules: string[] = [];
  readonly audioWorklet = {
    addModule: (url: string): Promise<void> => {
      this.modules.push(url);
      return Promise.resolve();
    },
  };

  constructor() {
    FakeAudioContext.created.push(this);
  }

  addEventListener(): void {}

  removeEventListener(): void {}

  close(): Promise<void> {
    this.state = 'closed';
    return Promise.resolve();
  }
}

/** Grants the pending `getUserMedia`. */
let grant: (stream: FakeStream) => void;

function setVisibility(state: DocumentVisibilityState): void {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: state });
}

beforeEach(() => {
  FakeAudioContext.created = [];
  vi.stubGlobal('AudioContext', FakeAudioContext);
  vi.stubGlobal('AudioWorkletNode', class {});
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: () =>
        new Promise<FakeStream>((resolve) => {
          grant = resolve;
        }),
    },
  });
  setVisibility('visible');
});

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, 'mediaDevices');
  Reflect.deleteProperty(document, 'visibilityState');
});

describe('WebAudioRecorder', () => {
  it.each([
    [
      'hidden',
      () => {
        setVisibility('hidden');
        document.dispatchEvent(new Event('visibilitychange'));
      },
    ],
    ['left', () => window.dispatchEvent(new Event('pagehide'))],
  ])(
    'a tab %s while the microphone is opening ends the start without going live',
    async (_, background) => {
      const recorder = new WebAudioRecorder();
      const reasons: InterruptReason[] = [];
      // What the controller does with an interrupt while it is still starting.
      const sink: RecorderSink = {
        samples: () => undefined,
        level: () => undefined,
        interrupted: (reason) => {
          reasons.push(reason);
          void recorder.stop();
        },
      };

      const starting = recorder.start(16_000, sink);
      background();
      const stream = new FakeStream();
      grant(stream);
      await starting;

      expect(reasons).toEqual(['background']);
      expect(stream.track.stopped).toBe(true);
      const [context] = FakeAudioContext.created;
      expect(context?.modules).toEqual([]);
      expect(context?.state).toBe('closed');
    },
  );
});
