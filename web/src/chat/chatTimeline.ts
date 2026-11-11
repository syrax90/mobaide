/**
 * The chat timeline reducer: snapshot / events / sync rules, pure.
 *
 * The relay broadcasts every frame to every client attached to the session, so
 * this one sees snapshots and replays that other clients asked for. The rules
 * that make that safe:
 *
 * - a snapshot replaces everything;
 * - an events frame from another epoch is ignored and answered with a fresh
 *   `sync` (→ snapshot);
 * - per event, `seq <= ours` is a duplicate, `ours + 1` applies, anything
 *   beyond is a gap: stop and ask for `sync{epoch, after_seq}`.
 *
 * `syncPending` keeps one request in flight. Frames that arrive before its
 * answer show the same gap and must not each send another one; the answer
 * covers them because the socket is ordered.
 */
import {
  INITIAL_CHAT_STATE,
  type ChatItem,
  type ChatState,
  type EventsFrame,
  type SnapshotFrame,
} from './chatWire';

/** Client memory cap; old steps go first, then the oldest items. */
export const MAX_CHAT_ITEMS = 1500;

export interface ChatTimeline {
  /** A snapshot has been applied at least once. */
  loaded: boolean;
  epoch: string | null;
  seq: number;
  /** Older messages were dropped, by the agent or by the client cap. */
  truncated: boolean;
  items: readonly ChatItem[];
  /** Item id → index in `items`. */
  positions: ReadonlyMap<string, number>;
  state: ChatState;
  syncPending: boolean;
}

export const INITIAL_CHAT_TIMELINE: ChatTimeline = {
  loaded: false,
  epoch: null,
  seq: 0,
  truncated: false,
  items: [],
  positions: new Map(),
  state: INITIAL_CHAT_STATE,
  syncPending: false,
};

/** What to put in a `sync` command: missed events after `seq`, or a snapshot. */
export type SyncRequest = { epoch: string; seq: number } | null;

export interface ChatReduction {
  timeline: ChatTimeline;
  /** `undefined` = send nothing; otherwise send a `sync` with this request. */
  sync?: SyncRequest;
}

function indexItems(items: readonly ChatItem[]): Map<string, number> {
  const positions = new Map<string, number>();
  items.forEach((item, index) => positions.set(item.id, index));
  return positions;
}

const isStep = (item: ChatItem): boolean => item.type === 'tool' || item.type === 'thought';

const isFinishedStep = (item: ChatItem): boolean =>
  item.type === 'thought' ||
  (item.type === 'tool' && item.status !== 'pending' && item.status !== 'in_progress');

/**
 * Trims `items` to `max`: finished steps go first, oldest first, then items
 * from the front; the newest item and running tools stay (a dropped tool's
 * next update would re-add it). `droppedMessages` = a non-step was dropped.
 */
export function trimChatItems(
  items: ChatItem[],
  max: number,
): { items: ChatItem[]; capped: boolean; droppedMessages: boolean } {
  let excess = items.length - max;
  if (excess <= 0) return { items, capped: false, droppedMessages: false };
  const last = items.length - 1;
  const drop = new Set<number>();
  for (let i = 0; i < last && excess > 0; i += 1) {
    const item = items[i];
    if (item !== undefined && isFinishedStep(item)) {
      drop.add(i);
      excess -= 1;
    }
  }
  let droppedMessages = false;
  for (let i = 0; i < last && excess > 0; i += 1) {
    const item = items[i];
    if (item === undefined || drop.has(i)) continue;
    drop.add(i);
    excess -= 1;
    if (!isStep(item)) droppedMessages = true;
  }
  return { items: items.filter((_, i) => !drop.has(i)), capped: drop.size > 0, droppedMessages };
}

export function resumeOf(timeline: ChatTimeline): SyncRequest {
  return timeline.epoch === null ? null : { epoch: timeline.epoch, seq: timeline.seq };
}

/** A socket (re)opened: always sync, resuming from what is held. */
export function chatOpened(timeline: ChatTimeline): ChatReduction {
  return { timeline: { ...timeline, syncPending: true }, sync: resumeOf(timeline) };
}

function requestSync(timeline: ChatTimeline, request: SyncRequest): ChatReduction {
  if (timeline.syncPending) return { timeline };
  return { timeline: { ...timeline, syncPending: true }, sync: request };
}

export function applySnapshot(timeline: ChatTimeline, frame: SnapshotFrame): ChatReduction {
  // Another client's fresh sync re-broadcasts what this one already holds;
  // keeping the item objects spares every row a re-render.
  if (timeline.loaded && timeline.epoch === frame.epoch && timeline.seq === frame.seq) {
    return { timeline: timeline.syncPending ? { ...timeline, syncPending: false } : timeline };
  }
  const { items, droppedMessages } = trimChatItems(frame.items, MAX_CHAT_ITEMS);
  return {
    timeline: {
      loaded: true,
      epoch: frame.epoch,
      seq: frame.seq,
      truncated: frame.truncated || droppedMessages,
      items,
      positions: indexItems(items),
      state: frame.state,
      syncPending: false,
    },
  };
}

export function applyEvents(timeline: ChatTimeline, frame: EventsFrame): ChatReduction {
  if (timeline.epoch === null) {
    return timeline.syncPending ? { timeline } : requestSync(timeline, null);
  }
  if (frame.epoch !== timeline.epoch) return requestSync(timeline, null);

  if (frame.events.length === 0) {
    if (frame.seq > timeline.seq) return requestSync(timeline, resumeOf(timeline));
    if (frame.seq === timeline.seq && timeline.syncPending) {
      return { timeline: { ...timeline, syncPending: false } };
    }
    return { timeline };
  }

  let seq = timeline.seq;
  let items: ChatItem[] | null = null;
  let positions: Map<string, number> | null = null;
  let state = timeline.state;
  let gap = false;

  for (const event of frame.events) {
    if (event.seq <= seq) continue;
    if (event.seq !== seq + 1) {
      gap = true;
      break;
    }
    seq = event.seq;
    switch (event.type) {
      case 'item': {
        const item = event.item;
        if (item === null) break;
        items ??= [...timeline.items];
        const at = (positions ?? timeline.positions).get(item.id);
        if (at === undefined) {
          positions ??= new Map(timeline.positions);
          positions.set(item.id, items.length);
          items.push(item);
        } else {
          items[at] = item;
        }
        break;
      }
      case 'append': {
        const at = (positions ?? timeline.positions).get(event.id);
        if (at === undefined || event.text === '') break;
        items ??= [...timeline.items];
        const target = items[at];
        if (target === undefined || !('text' in target)) break;
        items[at] = { ...target, text: target.text + event.text };
        break;
      }
      case 'remove': {
        const at = (positions ?? timeline.positions).get(event.id);
        if (at === undefined) break;
        items ??= [...timeline.items];
        items.splice(at, 1);
        // Rare (a setup notice going away): reindexing everything is fine.
        positions = indexItems(items);
        break;
      }
      case 'state':
        state = { ...state, ...event.patch };
        break;
      case 'skip':
        break;
    }
  }

  let next: ChatTimeline = { ...timeline, seq, state };
  if (items !== null) {
    const capped = trimChatItems(items, MAX_CHAT_ITEMS);
    next = {
      ...next,
      items: capped.items,
      positions: capped.capped ? indexItems(capped.items) : (positions ?? timeline.positions),
      truncated: timeline.truncated || capped.droppedMessages,
    };
  }
  if (gap) return requestSync(next, resumeOf(next));
  if (next.syncPending && seq >= frame.seq) next = { ...next, syncPending: false };
  return { timeline: next };
}
