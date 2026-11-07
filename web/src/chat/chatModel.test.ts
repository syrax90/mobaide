import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fakeScheduler,
  recordingSocketFactory,
  settle,
  type FakeSocket,
} from '../realtime/__testing__/fakeSocket';
import type { ChannelSend, WebSocketChannelOptions } from '../realtime/webSocketChannel';
import { CHAT_FRAME_TYPE } from './chatWire';

// The model builds its own channel; the test hands that channel the fake
// socket and clock, the seams `channels.test.ts` passes as overrides.
const seams = vi.hoisted<{
  overrides: Partial<Pick<WebSocketChannelOptions, 'socketFactory' | 'scheduler' | 'random'>>;
}>(() => ({ overrides: {} }));

vi.mock('../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve('agent-jwt'),
  clearAgentToken: () => undefined,
}));
vi.mock('../realtime/channels', async (importOriginal) => {
  const real = await importOriginal<typeof import('../realtime/channels')>();
  class ChatSessionChannel extends real.ChatSessionChannel {
    constructor(agentId: string, sessionId: string, onOpen: (send: ChannelSend) => void) {
      super(agentId, sessionId, onOpen, seams.overrides);
    }
  }
  return { ...real, ChatSessionChannel };
});

const { ChatSessionModel } = await import('./chatModel');

type Model = InstanceType<typeof ChatSessionModel>;
type Timers = ReturnType<typeof fakeScheduler>;

const COMMAND_TIMEOUT_MS = 30_000;
const SYNC_TIMEOUT_MS = 10_000;

const live: Model[] = [];

afterEach(() => {
  for (const model of live.splice(0)) model.dispose();
});

async function harness(): Promise<{
  model: Model;
  socket: FakeSocket;
  sockets: FakeSocket[];
  modelTimers: Timers;
}> {
  const { sockets, socketFactory } = recordingSocketFactory();
  // Two clocks, as in `sessionModel.test.ts`: the channel's drives reconnect
  // backoff, the model's drives its own timeouts.
  const channelTimers = fakeScheduler();
  const modelTimers = fakeScheduler();
  seams.overrides = { socketFactory, scheduler: channelTimers.scheduler, random: () => 1 };
  const model = new ChatSessionModel('a1', 's1', { scheduler: modelTimers.scheduler });
  live.push(model);

  await settle();
  const socket = sockets[0];
  if (socket === undefined) throw new Error('no socket was opened');
  socket.accept();
  return { model, socket, sockets, modelTimers };
}

const decoder = new TextDecoder();
const encoder = new TextEncoder();

function commands(socket: FakeSocket): { id: string; op: string }[] {
  return socket.sent
    .filter(
      (frame): frame is Uint8Array =>
        frame instanceof Uint8Array && frame[0] === CHAT_FRAME_TYPE.command,
    )
    .map((frame) => JSON.parse(decoder.decode(frame.subarray(1))) as { id: string; op: string });
}

function syncs(socket: FakeSocket): { id: string; op: string }[] {
  return commands(socket).filter((command) => command.op === 'sync');
}

function deliverFrame(socket: FakeSocket, frame: Record<string, unknown>): void {
  const json = encoder.encode(JSON.stringify(frame));
  socket.deliver(Uint8Array.from([CHAT_FRAME_TYPE.frame, ...json]).buffer);
}

function deliverResult(socket: FakeSocket, body: Record<string, unknown>): void {
  deliverFrame(socket, { t: 'result', ...body });
}

/** Fire the one armed timer with this delay; the model also arms others. */
function fire(timers: Timers, delayMs: number): void {
  const index = timers.pending.findIndex((entry) => entry.delayMs === delayMs);
  const entry = timers.pending[index];
  if (entry === undefined) throw new Error(`no ${String(delayMs)} ms timer is armed`);
  timers.pending.splice(index, 1);
  entry.fn();
}

describe('ChatSessionModel commands', () => {
  it('settles an unanswered command as lost after 30 s and opens a new socket', async () => {
    const { model, sockets, modelTimers } = await harness();
    const result = model.cancel();

    fire(modelTimers, COMMAND_TIMEOUT_MS);

    await expect(result).resolves.toEqual({
      ok: false,
      error: 'The connection dropped.',
      lost: true,
    });
    expect(model.state().connection).toBe('connecting');
    await settle();
    expect(sockets).toHaveLength(2);
  });

  it('an answer in time disarms the timeout', async () => {
    const { model, socket, modelTimers } = await harness();
    const result = model.cancel();
    const [cancel] = commands(socket).filter((command) => command.op === 'cancel');
    if (cancel === undefined) throw new Error('no cancel was sent');

    deliverResult(socket, { id: cancel.id, ok: true, data: { cancelled: true } });

    await expect(result).resolves.toEqual({ ok: true, data: { cancelled: true } });
    expect(modelTimers.pending.map((entry) => entry.delayMs)).not.toContain(COMMAND_TIMEOUT_MS);
  });

  it('a second restart while one is in flight shares its result', async () => {
    const { model, socket } = await harness();

    const first = model.restart();
    const second = model.restart();

    expect(second).toBe(first);
    expect(model.state().restarting).toBe(true);
    const restarts = commands(socket).filter((command) => command.op === 'restart');
    expect(restarts).toHaveLength(1);

    deliverResult(socket, { id: restarts[0]?.id, ok: true });

    await expect(first).resolves.toEqual({ ok: true, data: undefined });
    expect(model.state().restarting).toBe(false);
  });

  it('a session that left the agent list while on screen reads as gone', async () => {
    const { model } = await harness();
    expect(model.state().gone).toBe(false);

    model.markEvicted();

    expect(model.state().gone).toBe(true);
    expect(model.isGone()).toBe(true);
  });
});

describe('ChatSessionModel deadlines', () => {
  it('gives a large prompt 2 s more per 100 kB to reach the agent', async () => {
    const { model, modelTimers } = await harness();
    const result = model.prompt('look', [{ mime: 'image/png', data: 'A'.repeat(1_000_000) }]);

    const deadlines = modelTimers.pending.map((entry) => entry.delayMs);
    expect(deadlines).toContain(50_000);
    expect(deadlines).not.toContain(COMMAND_TIMEOUT_MS);

    fire(modelTimers, 50_000);
    await expect(result).resolves.toEqual({
      ok: false,
      error: 'The connection dropped.',
      lost: true,
    });
  });

  it('a silent sync timeout resumes again after 10 s and 20 s; the 40 s one reconnects', async () => {
    const { model, socket, sockets, modelTimers } = await harness();
    deliverFrame(socket, { t: 'snapshot', epoch: 'e1', seq: 3, items: [] });
    deliverFrame(socket, { t: 'events', epoch: 'e1', seq: 5, events: [] });
    expect(syncs(socket)).toHaveLength(2);

    fire(modelTimers, SYNC_TIMEOUT_MS);
    fire(modelTimers, 2 * SYNC_TIMEOUT_MS);

    expect(syncs(socket).slice(2)).toEqual([
      { id: expect.any(String) as string, op: 'sync', epoch: 'e1', after_seq: 3 },
      { id: expect.any(String) as string, op: 'sync', epoch: 'e1', after_seq: 3 },
    ]);
    expect(model.state().connection).toBe('live');

    fire(modelTimers, 4 * SYNC_TIMEOUT_MS);

    expect(model.state().connection).toBe('connecting');
    await settle();
    expect(sockets).toHaveLength(2);
  });

  it('any frame from the agent restarts the count of silent sync timeouts', async () => {
    const { model, socket, sockets, modelTimers } = await harness();
    fire(modelTimers, SYNC_TIMEOUT_MS);
    fire(modelTimers, 2 * SYNC_TIMEOUT_MS);

    deliverResult(socket, { id: 'not-ours', ok: true });
    fire(modelTimers, 4 * SYNC_TIMEOUT_MS);
    fire(modelTimers, 2 * SYNC_TIMEOUT_MS);

    expect(model.state().connection).toBe('live');
    // A snapshot request in flight is never sent twice.
    expect(syncs(socket)).toHaveLength(1);
    await settle();
    expect(sockets).toHaveLength(1);

    fire(modelTimers, 4 * SYNC_TIMEOUT_MS);

    expect(model.state().connection).toBe('connecting');
  });
});
