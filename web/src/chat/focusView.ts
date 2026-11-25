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
 * message, like a tool call. Cards with only thinking and tool calls between
 * them form a run; a run of two or more shows as one collapsible group at the
 * first card's place (`subagentEntries`), and a card that needs the user
 * stays out of the group so its request is never hidden.
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
    }
  | {
      kind: 'subagents';
      id: string;
      cards: readonly SubagentItem[];
      open: boolean;
    };

export interface TimelineRowOptions {
  focus: boolean;
  running: boolean;
  /** Tool calls a pending request (approval or question) waits on: always shown. */
  waitingTools: ReadonlySet<string>;
  /** Ids of the steps rows and subagent groups the user opened. */
  open: ReadonlySet<string>;
  /** Tells which subagent cards need the user; without it only the card's own state does. */
  tree?: SubagentTree;
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

/**
 * Subagent runs: cards with only thinking and tool calls between them. Returns
 * the items without the later cards of each run, and each run of two or more
 * by its first card's id.
 */
function subagentRuns(items: readonly ChatItem[]): {
  kept: readonly ChatItem[];
  runs: ReadonlyMap<string, readonly SubagentItem[]>;
} {
  const runs = new Map<string, SubagentItem[]>();
  const merged = new Set<ChatItem>();
  let run: SubagentItem[] | null = null;
  const close = () => {
    const first = run?.[0];
    if (run !== null && first !== undefined && run.length >= 2) {
      runs.set(first.id, run);
      for (const card of run.slice(1)) merged.add(card);
    }
    run = null;
  };
  for (const item of items) {
    if (item.type === 'subagent') {
      if (run === null) run = [item];
      else run.push(item);
    } else if (!isWork(item)) {
      close();
    }
  }
  close();
  if (merged.size === 0) return { kept: items, runs };
  return { kept: items.filter((item) => !merged.has(item)), runs };
}

export function timelineRows(items: readonly ChatItem[], options: TimelineRowOptions): TimelineRow[] {
  const { kept, runs } = subagentRuns(items);
  const rows: TimelineRow[] = [];
  const push = (item: ChatItem, nested: boolean) => {
    const run = runs.get(item.id);
    if (run === undefined) {
      rows.push({ kind: 'item', item, nested });
      return;
    }
    for (const entry of subagentEntries(run, options.tree, options.waitingTools)) {
      if (entry.kind === 'card') rows.push({ kind: 'item', item: entry.card, nested });
      else rows.push({ kind: 'subagents', id: entry.id, cards: entry.cards, open: options.open.has(entry.id) });
    }
  };
  if (!options.focus) {
    for (const item of kept) push(item, false);
    return rows;
  }
  const folded = foldedItems(kept, options.waitingTools);
  const lastIndex = kept.length - 1;
  let index = 0;
  while (index <= lastIndex) {
    const item = kept[index];
    if (item === undefined) break;
    if (folded[index] !== true) {
      push(item, false);
      index += 1;
      continue;
    }
    const group: ChatItem[] = [];
    while (index <= lastIndex && folded[index] === true) {
      const step = kept[index];
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

export type SubagentEntry =
  | { kind: 'card'; card: SubagentItem }
  | { kind: 'group'; id: string; cards: readonly SubagentItem[] };

export function subagentGroupId(firstCardId: string): string {
  return `subagents:${firstCardId}`;
}

/** The key that marks a subagent card open, in the same set as the steps rows and groups. */
export function subagentCardKey(cardId: string): string {
  return `subagent:${cardId}`;
}

/** The card, a tool call it waits on, or a card nested in it waits for the user. */
export function subagentNeedsYou(
  card: SubagentItem,
  tree: SubagentTree | undefined,
  waitingTools: ReadonlySet<string>,
): boolean {
  if (card.state === 'requires_action') return true;
  if (tree === undefined) return false;
  if ((tree.tools.get(card.subagent_id) ?? []).some((tool) => waitingTools.has(tool.id))) return true;
  return (tree.children.get(card.subagent_id) ?? []).some((child) => subagentNeedsYou(child, tree, waitingTools));
}

/**
 * Sibling cards as shown: two or more calm cards form one group, keyed by the
 * first card of all; the cards that need the user follow it on their own.
 * With fewer than two calm cards every card shows on its own, in order.
 */
export function subagentEntries(
  cards: readonly SubagentItem[],
  tree: SubagentTree | undefined,
  waitingTools: ReadonlySet<string>,
): SubagentEntry[] {
  const first = cards[0];
  const urgent = cards.filter((card) => subagentNeedsYou(card, tree, waitingTools));
  const calm = urgent.length === 0 ? cards : cards.filter((card) => !urgent.includes(card));
  if (first === undefined || calm.length < 2) return cards.map((card) => ({ kind: 'card', card }));
  return [
    { kind: 'group', id: subagentGroupId(first.id), cards: calm },
    ...urgent.map((card): SubagentEntry => ({ kind: 'card', card })),
  ];
}

export function subagentsLabel(count: number): string {
  return count === 1 ? '1 subagent' : `${String(count)} subagents`;
}

/** "2 running · 5 done · 1 failed": the states of a group's cards, busiest first. */
export function subagentGroupSummary(cards: readonly SubagentItem[]): string {
  const counts = { running: 0, needs: 0, done: 0, failed: 0, stopped: 0 };
  for (const card of cards) {
    if (card.state === 'running') counts.running += 1;
    else if (card.state === 'requires_action') counts.needs += 1;
    else if (card.state === 'idle') {
      if (card.stop_reason === 'cancelled') counts.stopped += 1;
      else if (card.stop_reason === 'error') counts.failed += 1;
      else counts.done += 1;
    }
  }
  const parts: string[] = [];
  if (counts.needs > 0) parts.push(`${String(counts.needs)} need you`);
  if (counts.running > 0) parts.push(`${String(counts.running)} running`);
  if (counts.done > 0) parts.push(`${String(counts.done)} done`);
  if (counts.failed > 0) parts.push(`${String(counts.failed)} failed`);
  if (counts.stopped > 0) parts.push(`${String(counts.stopped)} stopped`);
  return parts.join(' · ');
}

/** The first line of a result as plain text, for a collapsed card; null when it is blank. */
export function resultPreview(result: string | null): string | null {
  if (result === null) return null;
  for (const raw of result.split('\n')) {
    const line = raw
      .replace(/^\s*(#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)/, '')
      .replace(/\*\*|__|`/g, '')
      .trim();
    if (line !== '' && !/^(-{3,}|\*{3,}|_{3,})$/.test(line)) return line;
  }
  return null;
}
