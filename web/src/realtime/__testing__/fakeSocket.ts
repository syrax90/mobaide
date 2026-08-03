/**
 * Shared doubles for the realtime channel tests.
 *
 * Test-only, in the same spirit as `src/protocol/__fixtures__/load.ts`: nothing
 * under `src/` outside a `*.test.ts` may import it. It exists because
 * `webSocketChannel.test.ts` and `channels.test.ts` both need to play the
 * server half of a socket by hand, and two hand-rolled copies had already begun
 * to drift.
 */
import type { Scheduler, WebSocketLike } from '../webSocketChannel';

export const READY_CONNECTING = 0;
export const READY_OPEN = 1;
export const READY_CLOSED = 3;

/** Stand-in for the browser socket; the test drives the server half. */
export class FakeSocket implements WebSocketLike {
  readonly url: string;
  readonly sent: (string | ArrayBufferView)[] = [];
  /** Codes this side passed to `close()` — the WHATWG range rule is asserted on it. */
  readonly clientCloses: (number | undefined)[] = [];
  readyState = READY_CONNECTING;
  binaryType = 'blob';
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
  }

  send(data: string | ArrayBufferView): void {
    this.sent.push(data);
  }

  close(code?: number): void {
    this.clientCloses.push(code);
    this.readyState = READY_CLOSED;
  }

  accept(): void {
    this.readyState = READY_OPEN;
    this.onopen?.();
  }

  deliver(data: unknown): void {
    this.onmessage?.({ data });
  }

  serverClose(code: number, reason = ''): void {
    this.readyState = READY_CLOSED;
    this.onclose?.({ code, reason });
  }

  fail(): void {
    this.readyState = READY_CLOSED;
    this.onerror?.();
  }
}

/** Timer seam driven by the test, so no backoff delay is ever really slept. */
export function fakeScheduler() {
  const pending: { fn: () => void; delayMs: number }[] = [];
  const scheduler: Scheduler = {
    schedule(fn, delayMs) {
      const entry = { fn, delayMs };
      pending.push(entry);
      return () => {
        const index = pending.indexOf(entry);
        if (index >= 0) pending.splice(index, 1);
      };
    },
  };
  return {
    scheduler,
    pending,
    /** Fire the armed reconnect and report the delay it was armed with. */
    runNext(): number {
      const next = pending.shift();
      if (next === undefined) throw new Error('no reconnect was scheduled');
      next.fn();
      return next.delayMs;
    },
  };
}

/** Collects every socket a channel builds, for the `socketFactory` seam. */
export function recordingSocketFactory() {
  const sockets: FakeSocket[] = [];
  return {
    sockets,
    socketFactory: (url: string): WebSocketLike => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket;
    },
  };
}

/** `buildUrl` is awaited, so a socket only exists after the queue drains. */
export function settle(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}
