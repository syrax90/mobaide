import { describe, expect, it } from 'vitest';
import {
  INITIAL_CHAT_TIMELINE,
  MAX_CHAT_ITEMS,
  applyEvents,
  applySnapshot,
  chatOpened,
  type ChatTimeline,
} from './chatTimeline';
import {
  INITIAL_CHAT_STATE,
  type ChatEvent,
  type ChatItem,
  type EventsFrame,
  type SnapshotFrame,
  type ToolItem,
} from './chatWire';

const EPOCH = 'e1';

function agent(id: string, text = ''): ChatItem {
  return { id, type: 'agent', text };
}

function tool(id: string, status: ToolItem['status'] = 'pending'): ToolItem {
  return {
    id,
    type: 'tool',
    title: 'Run tests',
    kind: 'execute',
    status,
    subtitle: null,
    locations: [],
    content: [],
    input: null,
  };
}

function snapshot(seq: number, items: ChatItem[], epoch = EPOCH): SnapshotFrame {
  return { t: 'snapshot', epoch, seq, truncated: false, state: INITIAL_CHAT_STATE, items };
}

function events(list: ChatEvent[], epoch = EPOCH): EventsFrame {
  const last = list.at(-1);
  return { t: 'events', epoch, seq: last === undefined ? 0 : last.seq, events: list };
}

function loaded(seq: number, items: ChatItem[]): ChatTimeline {
  return applySnapshot(INITIAL_CHAT_TIMELINE, snapshot(seq, items)).timeline;
}

function textOf(timeline: ChatTimeline, id: string): string | null {
  const item = timeline.items[timeline.positions.get(id) ?? -1];
  return item !== undefined && 'text' in item ? item.text : null;
}

describe('applySnapshot', () => {
  it('replaces everything and clears a pending sync', () => {
    const pending = chatOpened(loaded(3, [agent('m:1', 'old')])).timeline;
    const { timeline } = applySnapshot(pending, snapshot(7, [agent('m:2', 'new')], 'e2'));

    expect(timeline.epoch).toBe('e2');
    expect(timeline.seq).toBe(7);
    expect(timeline.items.map((item) => item.id)).toEqual(['m:2']);
    expect(timeline.positions.get('m:2')).toBe(0);
    expect(timeline.syncPending).toBe(false);
    expect(timeline.loaded).toBe(true);
  });

  it('keeps the held items when another client re-syncs the same point', () => {
    const held = loaded(5, [agent('m:1', 'hi')]);

    const { timeline } = applySnapshot(held, snapshot(5, [agent('m:1', 'hi')]));

    expect(timeline).toBe(held);
  });

  it('keeps only the newest items over the client cap', () => {
    const many = Array.from({ length: MAX_CHAT_ITEMS + 10 }, (_, index) => agent(`m:${index}`));

    const { timeline } = applySnapshot(INITIAL_CHAT_TIMELINE, snapshot(1, many));

    expect(timeline.items).toHaveLength(MAX_CHAT_ITEMS);
    expect(timeline.items[0]?.id).toBe('m:10');
    expect(timeline.positions.get('m:10')).toBe(0);
    expect(timeline.truncated).toBe(true);
  });
});

describe('applyEvents', () => {
  it('asks for a snapshot once when events come before any snapshot', () => {
    const first = applyEvents(INITIAL_CHAT_TIMELINE, events([{ type: 'state', seq: 1, patch: {} }]));
    expect(first.sync).toBeNull();
    expect(first.timeline.syncPending).toBe(true);

    const second = applyEvents(first.timeline, events([{ type: 'state', seq: 2, patch: {} }]));
    expect(second.sync).toBeUndefined();
  });

  it('upserts by id, appends text and merges state', () => {
    const start = loaded(2, [agent('m:1', 'Hel')]);

    const { timeline, sync } = applyEvents(
      start,
      events([
        { type: 'append', seq: 3, id: 'm:1', text: 'lo' },
        { type: 'item', seq: 4, item: tool('call-1') },
        { type: 'item', seq: 5, item: tool('call-1', 'completed') },
        { type: 'state', seq: 6, patch: { status: 'running', cancelling: true } },
      ]),
    );

    expect(sync).toBeUndefined();
    expect(timeline.seq).toBe(6);
    expect(textOf(timeline, 'm:1')).toBe('Hello');
    expect(timeline.items.map((item) => item.id)).toEqual(['m:1', 'call-1']);
    const updated = timeline.items[1];
    expect(updated?.type === 'tool' ? updated.status : null).toBe('completed');
    expect(timeline.state.status).toBe('running');
    expect(timeline.state.cancelling).toBe(true);
    expect(start.items.map((item) => item.id)).toEqual(['m:1']);
  });

  it('ignores appends that have no text target and skips unknown items', () => {
    const start = loaded(1, [tool('call-1')]);

    const { timeline } = applyEvents(
      start,
      events([
        { type: 'append', seq: 2, id: 'call-1', text: 'x' },
        { type: 'append', seq: 3, id: 'missing', text: 'x' },
        { type: 'item', seq: 4, item: null },
        { type: 'skip', seq: 5 },
      ]),
    );

    expect(timeline.seq).toBe(5);
    expect(timeline.items).toEqual(start.items);
  });

  it('removes by id and keeps the later events of the frame on the right items', () => {
    const start = loaded(1, [agent('n:1', 'Setting up'), agent('m:2', 'a')]);

    const { timeline, sync } = applyEvents(
      start,
      events([
        { type: 'item', seq: 2, item: agent('n:3', 'brief') },
        { type: 'remove', seq: 3, id: 'n:1' },
        { type: 'remove', seq: 4, id: 'n:3' },
        { type: 'append', seq: 5, id: 'm:2', text: 'b' },
        { type: 'item', seq: 6, item: agent('m:4', 'c') },
        { type: 'remove', seq: 7, id: 'missing' },
      ]),
    );

    expect(sync).toBeUndefined();
    expect(timeline.seq).toBe(7);
    expect(timeline.items.map((item) => item.id)).toEqual(['m:2', 'm:4']);
    expect([...timeline.positions]).toEqual([
      ['m:2', 0],
      ['m:4', 1],
    ]);
    expect(textOf(timeline, 'm:2')).toBe('ab');
    expect(start.items.map((item) => item.id)).toEqual(['n:1', 'm:2']);
  });

  it('applies what came before a gap and asks for the rest once', () => {
    const start = loaded(1, [agent('m:1', 'a')]);

    const gap = applyEvents(
      start,
      events([
        { type: 'append', seq: 2, id: 'm:1', text: 'b' },
        { type: 'append', seq: 4, id: 'm:1', text: 'd' },
      ]),
    );
    expect(gap.sync).toEqual({ epoch: EPOCH, seq: 2 });
    expect(textOf(gap.timeline, 'm:1')).toBe('ab');

    const again = applyEvents(gap.timeline, events([{ type: 'append', seq: 5, id: 'm:1', text: 'e' }]));
    expect(again.sync).toBeUndefined();
    expect(again.timeline.seq).toBe(2);
  });

  it('treats an empty frame that is ahead as missed events', () => {
    const start = loaded(3, []);

    const ahead = applyEvents(start, { t: 'events', epoch: EPOCH, seq: 9, events: [] });

    expect(ahead.sync).toEqual({ epoch: EPOCH, seq: 3 });
  });

  it('clears the pending sync on the empty ack and on catching up', () => {
    const opened = chatOpened(loaded(3, [agent('m:1')])).timeline;
    const acked = applyEvents(opened, { t: 'events', epoch: EPOCH, seq: 3, events: [] });
    expect(acked.timeline.syncPending).toBe(false);

    const replayed = applyEvents(opened, events([{ type: 'append', seq: 4, id: 'm:1', text: 'x' }]));
    expect(replayed.timeline.syncPending).toBe(false);
  });

  it('drops the oldest items over the client cap', () => {
    const full = loaded(
      1,
      Array.from({ length: MAX_CHAT_ITEMS }, (_, index) => agent(`m:${index}`)),
    );

    const { timeline } = applyEvents(full, events([{ type: 'item', seq: 2, item: agent('m:new', 'x') }]));

    expect(timeline.items).toHaveLength(MAX_CHAT_ITEMS);
    expect(timeline.items[0]?.id).toBe('m:1');
    expect(timeline.positions.get('m:new')).toBe(MAX_CHAT_ITEMS - 1);
    expect(timeline.positions.has('m:0')).toBe(false);
    expect(timeline.truncated).toBe(true);
  });
});

describe('chatOpened', () => {
  it('asks for a snapshot when nothing is held', () => {
    expect(chatOpened(INITIAL_CHAT_TIMELINE).sync).toBeNull();
  });
});
