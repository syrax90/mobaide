// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { readFixture } from './__fixtures__/load';
import { chatSupport, agentVersionSchema } from './dto/agentVersion';
import {
  CHAT_FRAME_TYPE,
  decodeChatInbound,
  encodeChatCommand,
  formResponseCommand,
  promptCommand,
  syncCommand,
  type ChatCommand,
  type ChatFrame,
  type EventsFrame,
  type SnapshotFrame,
} from '../chat/chatWire';
import { INITIAL_CHAT_TIMELINE, applyEvents, applySnapshot, chatOpened } from '../chat/chatTimeline';

interface CommandCase {
  name: string;
  command: Record<string, unknown> & { id: string };
}

interface ServerCase {
  name: string;
  frame: unknown;
}

interface ChatFramesFixture {
  commands: CommandCase[];
  server: ServerCase[];
}

const fixture = readFixture<ChatFramesFixture>('frames/chat_frames.json');
const encoder = new TextEncoder();
const decoder = new TextDecoder();

/** Each golden command, rebuilt through the client's own builders. */
const BUILDERS: Readonly<Record<string, () => ChatCommand>> = {
  sync_fresh: () => syncCommand(null),
  sync_resume: () => syncCommand({ epoch: '9f3a1c2b7d4e', seq: 41 }),
  prompt_text: () => promptCommand('Fix the failing test', []),
  prompt_with_image: () =>
    promptCommand('What is on this screen?', [{ mime: 'image/png', data: 'iVBORw0KGgo=' }]),
  cancel: () => ({ op: 'cancel' }),
  set_config_select: () => ({ op: 'set_config', config_id: 'model', value: 'sonnet' }),
  set_config_boolean: () => ({ op: 'set_config', config_id: 'fast', value: true }),
  respond_permission: () => ({ op: 'respond', request_id: 'r:9', option_id: 'allow' }),
  respond_form_accept: () =>
    formResponseCommand('r:12', 'accept', { framework: 'vue', extras: ['lint'], strict: false }),
  respond_form_decline: () => formResponseCommand('r:12', 'decline'),
  dequeue: () => ({ op: 'dequeue', queue_id: 'q:14' }),
  restart: () => ({ op: 'restart' }),
  steer: () => ({ op: 'steer', queue_id: 'q:14' }),
};

function agentBytes(frame: unknown): Uint8Array {
  const json = encoder.encode(JSON.stringify(frame));
  const out = new Uint8Array(json.length + 1);
  out[0] = CHAT_FRAME_TYPE.frame;
  out.set(json, 1);
  return out;
}

function decodeServer(name: string): ChatFrame {
  const entry = fixture.server.find((candidate) => candidate.name === name);
  if (entry === undefined) throw new Error(`no server case ${name}`);
  const inbound = decodeChatInbound(agentBytes(entry.frame));
  if (inbound?.kind !== 'frame') throw new Error(`${name} did not decode`);
  return inbound.frame;
}

describe('chat command golden fixtures', () => {
  it('has a builder for every golden command', () => {
    expect(fixture.commands.map((entry) => entry.name).sort()).toEqual(Object.keys(BUILDERS).sort());
  });

  it.each(fixture.commands)('encodes $name', ({ name, command }) => {
    const build = BUILDERS[name];
    expect(build, name).toBeDefined();
    const bytes = encodeChatCommand(command.id, build?.() ?? { op: 'cancel' });
    expect(bytes[0]).toBe(0x63);
    expect(JSON.parse(decoder.decode(bytes.subarray(1)))).toEqual(command);
  });
});

describe('chat frame golden fixtures', () => {
  it('decodes every server frame', () => {
    for (const entry of fixture.server) {
      expect(decodeChatInbound(agentBytes(entry.frame))?.kind, entry.name).toBe('frame');
    }
  });

  it('decodes the full snapshot into view state', () => {
    const frame = decodeServer('snapshot_full') as SnapshotFrame;
    expect(frame.t).toBe('snapshot');
    expect(frame.epoch).toBe('9f3a1c2b7d4e');
    expect(frame.seq).toBe(41);
    expect(frame.items.map((item) => item.type)).toEqual([
      'user',
      'thought',
      'agent',
      'tool',
      'tool',
      'notice',
      'compaction',
      'turn_end',
      'error',
    ]);
    const state = frame.state;
    expect(state.status).toBe('running');
    expect(state.agent?.title).toBe('Claude Agent');
    expect(state.config.map((option) => [option.id, option.type, option.value])).toEqual([
      ['mode', 'select', 'default'],
      ['model', 'select', 'opus'],
      ['fast', 'boolean', false],
    ]);
    expect(state.commands.map((command) => command.name)).toEqual(['review', 'compact']);
    expect(state.usage).toEqual({ used: 53000, size: 200000, cost: { amount: 0.045, currency: 'USD' } });
    expect(state.plan.map((entry) => entry.status)).toEqual(['completed', 'in_progress', 'pending']);
    expect(state.requests.map((request) => request.kind)).toEqual(['permission', 'form']);
    const form = state.requests[1];
    expect(form?.kind === 'form' ? form.fields.map((field) => field.type) : []).toEqual([
      'select',
      'multiselect',
      'text',
      'boolean',
      'integer',
    ]);
    expect(state.queue).toEqual([{ id: 'q:14', text: 'Also update the README', images: 0 }]);
    expect(state.prompt_caps.image).toBe(true);
    expect(state.steering).toBe(true);
    const tool = frame.items[3];
    expect(tool?.type === 'tool' ? tool.content[0]?.type : null).toBe('diff');
  });

  it('applies the events batch on top of the snapshot', () => {
    const snapshot = decodeServer('snapshot_full') as SnapshotFrame;
    const events = decodeServer('events_batch') as EventsFrame;
    const loaded = applySnapshot(INITIAL_CHAT_TIMELINE, snapshot).timeline;
    const { timeline, sync } = applyEvents(loaded, events);
    expect(sync).toBeUndefined();
    expect(timeline.seq).toBe(45);
    expect(timeline.items).toHaveLength(snapshot.items.length + 2);
    const streamed = timeline.items[timeline.positions.get('m:8') ?? -1];
    expect(streamed?.type === 'agent' ? streamed.text : null).toBe('Done: the tests pass.');
    expect(timeline.state.status).toBe('ready');
    expect(timeline.state.requests).toEqual([]);
    expect(timeline.state.queue).toHaveLength(1);

    const ack = decodeServer('events_empty_ack') as EventsFrame;
    expect(applyEvents(timeline, ack).timeline).toBe(timeline);
  });

  it('follows the sync rules', () => {
    const snapshot = decodeServer('snapshot_full') as SnapshotFrame;
    const events = decodeServer('events_batch') as EventsFrame;
    const loaded = applySnapshot(INITIAL_CHAT_TIMELINE, snapshot).timeline;

    const opened = chatOpened(loaded);
    expect(opened.sync).toEqual({ epoch: '9f3a1c2b7d4e', seq: 41 });

    const replayed = applyEvents(applyEvents(loaded, events).timeline, events);
    expect(replayed.timeline.seq).toBe(45);
    expect(replayed.sync).toBeUndefined();

    const gap = applyEvents(loaded, { ...events, events: events.events.slice(1) });
    expect(gap.sync).toEqual({ epoch: '9f3a1c2b7d4e', seq: 41 });
    expect(gap.timeline.seq).toBe(41);
    expect(applyEvents(gap.timeline, { ...events, events: events.events.slice(2) }).sync).toBeUndefined();

    const otherEpoch = applyEvents(loaded, { ...events, epoch: 'other' });
    expect(otherEpoch.sync).toBeNull();
    expect(otherEpoch.timeline.items).toBe(loaded.items);
  });

  it('decodes results', () => {
    expect(decodeServer('result_ok')).toEqual({
      t: 'result',
      id: 'c-3',
      ok: true,
      data: { queued: true, queue_id: 'q:14' },
      error: null,
    });
    expect(decodeServer('result_error')).toMatchObject({
      ok: false,
      error: 'This request was already answered',
    });
  });

  it('ignores the PTY replay frames sent for older clients', () => {
    for (const type of [0x62, 0x6f, 0x65]) {
      expect(decodeChatInbound(new Uint8Array([type, 0x41]))).toBeNull();
    }
  });
});

describe('chat capability', () => {
  const versions = readFixture<{ cases: { name: string; wire: unknown }[] }>(
    'dto/agent/VersionInfoResponse.json',
  );

  it('reads capabilities.chat', () => {
    const withChat = versions.cases.find((entry) => entry.name === 'chat_capability');
    const info = agentVersionSchema.parse(withChat?.wire);
    expect(chatSupport(info, 'claude')).toEqual({ kind: 'available', resume: true, live: false });
    expect(chatSupport(info, 'codex')).toEqual({ kind: 'available', resume: true, live: true });
    expect(chatSupport(info, 'antigravity')).toEqual({ kind: 'unavailable' });
    expect(chatSupport(agentVersionSchema.parse({ ...info, capabilities: {} }), 'claude')).toEqual({
      kind: 'outdated',
    });
    expect(chatSupport(undefined, 'claude')).toEqual({ kind: 'unknown' });
  });
});
