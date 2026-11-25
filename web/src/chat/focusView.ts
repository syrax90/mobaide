/**
 * Focus view: the timeline as answers and questions, with the work between
 * them folded into one "steps" row per run.
 *
 * Folded ("steps"): thinking, tool calls, and agent messages that more work
 * follows in the same turn. Shown in place: user messages, the agent messages
 * that end a turn (the answer), tool calls waiting for approval, notices,
 * errors, compaction and turn-end lines. A turn ends at the next user message
 * or notice: a turn sent from another app on the PC starts with a notice, not
 * with a user message, and must not fold the answer before it.
 *
 * Subagents: the tool calls a subagent card owns and the cards nested in
 * another card leave the list first (`subagentTree`), then the rest folds. A
 * card is shown in place, never folded, and counts as work after an agent
 * message, like a tool call.
 *
 * Mirrored by Android `ChatFocusView.kt` and iOS `ChatFocusView.swift`.
 */
import type { ChatItem, SubagentItem, ToolItem } from './chatWire';

export type TimelineRow =
  | { kind: 'item'; item: ChatItem; nested: boolean }
  | {
      kind: 'steps';
      id: string;
      count: number;
      /** The run is the newest thing in a running turn. */
      live: boolean;
      /** What the newest step does ("Thinking", a tool title); null unless live. */
      current: string | null;
      open: boolean;
    };

export interface TimelineRowOptions {
  focus: boolean;
  running: boolean;
  /** Tool calls a pending request (approval or question) waits on: always shown. */
  waitingTools: ReadonlySet<string>;
  /** Ids of the steps rows the user opened. */
  open: ReadonlySet<string>;
}

export function stepsRowId(firstItemId: string): string {
  return `steps:${firstItemId}`;
}

function isWork(item: ChatItem): boolean {
  return item.type === 'thought' || item.type === 'tool';
}

function stepLabel(item: ChatItem): string {
  switch (item.type) {
    case 'tool':
      return item.title === '' ? 'Tool call' : item.title;
    case 'thought':
      return 'Thinking';
    case 'agent':
      return 'Writing';
    default:
      return 'Working';
  }
}

/** Which items fold into a steps row. */
function foldedItems(items: readonly ChatItem[], waitingTools: ReadonlySet<string>): boolean[] {
  const folded = new Array<boolean>(items.length).fill(false);
  let workAfter = false;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item === undefined) continue;
    if (item.type === 'user' || item.type === 'notice') {
      workAfter = false;
    } else if (item.type === 'agent') {
      folded[index] = workAfter;
    } else if (item.type === 'subagent') {
      workAfter = true;
    } else if (isWork(item)) {
      folded[index] = !(item.type === 'tool' && waitingTools.has(item.id));
      workAfter = true;
    }
  }
  return folded;
}

export function timelineRows(items: readonly ChatItem[], options: TimelineRowOptions): TimelineRow[] {
  if (!options.focus) return items.map((item) => ({ kind: 'item', item, nested: false }));
  const folded = foldedItems(items, options.waitingTools);
  const rows: TimelineRow[] = [];
  const lastIndex = items.length - 1;
  let index = 0;
  while (index <= lastIndex) {
    const item = items[index];
    if (item === undefined) break;
    if (folded[index] !== true) {
      rows.push({ kind: 'item', item, nested: false });
      index += 1;
      continue;
    }
    const group: ChatItem[] = [];
    while (index <= lastIndex && folded[index] === true) {
      const step = items[index];
      if (step !== undefined) group.push(step);
      index += 1;
    }
    const first = group[0];
    const newest = group[group.length - 1];
    if (first === undefined || newest === undefined) continue;
    const id = stepsRowId(first.id);
    const live = options.running && index > lastIndex;
    const open = options.open.has(id);
    rows.push({ kind: 'steps', id, count: group.length, live, current: live ? stepLabel(newest) : null, open });
    if (open) {
      for (const step of group) rows.push({ kind: 'item', item: step, nested: true });
    }
  }
  return rows;
}

export interface SubagentTree {
  /** The items of the main flow: `items` itself when no subagent card hides any. */
  visible: readonly ChatItem[];
  /** Subagent id → the tool calls its card shows, in timeline order. */
  tools: ReadonlyMap<string, readonly ToolItem[]>;
  /** Subagent id → the cards nested in its card, in timeline order. */
  children: ReadonlyMap<string, readonly SubagentItem[]>;
}

/**
 * Which items move into a subagent card. A card nests in its parent's card
 * only when the parent card is present and the parent chain does not loop
 * back; otherwise it stays in the main flow. Tool calls of a subagent with no
 * card stay in the main flow.
 */
export function subagentTree(items: readonly ChatItem[]): SubagentTree {
  const cards = new Map<string, SubagentItem>();
  for (const item of items) {
    if (item.type === 'subagent' && item.subagent_id !== '') cards.set(item.subagent_id, item);
  }
  const tools = new Map<string, ToolItem[]>();
  const children = new Map<string, SubagentItem[]>();
  if (cards.size === 0) return { visible: items, tools, children };

  const nestedIn = (card: SubagentItem): string | null => {
    const parent = card.parent_subagent_id;
    if (parent === null || !cards.has(parent)) return null;
    const seen = new Set<string>([card.subagent_id]);
    let current: string | null = parent;
    while (current !== null) {
      if (seen.has(current)) return null;
      seen.add(current);
      const above: string | null = cards.get(current)?.parent_subagent_id ?? null;
      current = above !== null && cards.has(above) ? above : null;
    }
    return parent;
  };

  const visible: ChatItem[] = [];
  for (const item of items) {
    if (item.type === 'tool' && typeof item.subagent_id === 'string' && cards.has(item.subagent_id)) {
      const owned = tools.get(item.subagent_id);
      if (owned === undefined) tools.set(item.subagent_id, [item]);
      else owned.push(item);
      continue;
    }
    if (item.type === 'subagent' && cards.get(item.subagent_id) === item) {
      const parent = nestedIn(item);
      if (parent !== null) {
        const siblings = children.get(parent);
        if (siblings === undefined) children.set(parent, [item]);
        else siblings.push(item);
        continue;
      }
    }
    visible.push(item);
  }
  return { visible: visible.length === items.length ? items : visible, tools, children };
}

export function stepsLabel(count: number): string {
  return count === 1 ? '1 step' : `${String(count)} steps`;
}
