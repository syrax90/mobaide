import { describe, expect, it } from 'vitest';
import type { ChatItem } from './chatWire';
import { stepsRowId, subagentTree, timelineRows, type TimelineRow, type TimelineRowOptions } from './focusView';

function user(id: string): ChatItem {
  return { id, type: 'user', text: 'Fix the login bug', attachments: [], at: null };
}

function agent(id: string): ChatItem {
  return { id, type: 'agent', text: 'Done' };
}

function thought(id: string): ChatItem {
  return { id, type: 'thought', text: 'Reading the code' };
}

function tool(id: string, title = 'Run tests'): ChatItem {
  return {
    id,
    type: 'tool',
    title,
    kind: 'execute',
    status: 'in_progress',
    subtitle: null,
    locations: [],
    content: [],
    input: null,
  };
}

function ownedTool(id: string, subagentId: string): ChatItem {
  return { ...tool(id), subagent_id: subagentId } as ChatItem;
}

function card(subagentId: string, parent: string | null = null): ChatItem {
  return {
    id: `sa:${subagentId}`,
    type: 'subagent',
    subagent_id: subagentId,
    parent_subagent_id: parent,
    title: 'Explore',
    description: null,
    task: null,
    state: 'running',
    stop_reason: null,
    result: null,
    can_cancel: false,
    plan: [],
  };
}

function notice(id: string): ChatItem {
  return { id, type: 'notice', severity: 'info', title: 'Loaded', text: '' };
}

function compaction(id: string): ChatItem {
  return { id, type: 'compaction', status: 'completed', text: '' };
}

function error(id: string): ChatItem {
  return { id, type: 'error', text: 'The agent exited' };
}

function turnEnd(id: string): ChatItem {
  return { id, type: 'turn_end', stop_reason: 'end_turn', text: '' };
}

function rows(items: ChatItem[], options: Partial<TimelineRowOptions> = {}): TimelineRow[] {
  return timelineRows(items, {
    focus: true,
    running: false,
    waitingTools: new Set(),
    open: new Set(),
    ...options,
  });
}

/** Item rows by id (nested ones indented), steps rows as `<id> ×<count>`. */
function outline(list: TimelineRow[]): string[] {
  return list.map((row) => {
    if (row.kind === 'steps') return `${row.id} ×${String(row.count)}`;
    return row.nested ? `  ${row.item.id}` : row.item.id;
  });
}

function stepsRows(list: TimelineRow[]): Extract<TimelineRow, { kind: 'steps' }>[] {
  return list.filter((row): row is Extract<TimelineRow, { kind: 'steps' }> => row.kind === 'steps');
}

describe('timelineRows', () => {
  it('shows every item in place when focus view is off', () => {
    const items = [user('u:1'), thought('t:2'), tool('tool-3'), agent('m:4')];

    const list = rows(items, { focus: false, running: true });

    expect(list).toEqual(items.map((item) => ({ kind: 'item', item, nested: false })));
  });

  it('folds the work of a turn into one steps row and keeps the question and the answer', () => {
    const list = rows([
      user('u:1'),
      thought('t:2'),
      agent('m:3'),
      tool('tool-4'),
      agent('m:5'),
      turnEnd('end:6'),
    ]);

    expect(outline(list)).toEqual(['u:1', 'steps:t:2 ×3', 'm:5', 'end:6']);
  });

  it('keeps the answer of a turn in place when the next turn does more work', () => {
    const list = rows([user('u:1'), tool('tool-2'), agent('m:3'), user('u:4'), tool('tool-5'), agent('m:6')]);

    expect(outline(list)).toEqual(['u:1', 'steps:tool-2 ×1', 'm:3', 'u:4', 'steps:tool-5 ×1', 'm:6']);
  });

  it('shows a tool call that waits for approval and splits the run around it', () => {
    const list = rows([user('u:1'), thought('t:2'), tool('tool-3'), tool('tool-4')], {
      waitingTools: new Set(['tool-3']),
    });

    expect(outline(list)).toEqual(['u:1', 'steps:t:2 ×1', 'tool-3', 'steps:tool-4 ×1']);
  });

  it('shows notices, compaction and errors in place between runs', () => {
    const list = rows([
      user('u:1'),
      thought('t:2'),
      notice('n:3'),
      tool('tool-4'),
      compaction('cmp-5'),
      error('err:6'),
    ]);

    expect(outline(list)).toEqual(['u:1', 'steps:t:2 ×1', 'n:3', 'steps:tool-4 ×1', 'cmp-5', 'err:6']);
  });

  it('ends a turn at a notice like at a user message, so the answer before it stays in place', () => {
    const fromAnotherApp = rows([user('u:1'), agent('m:2'), notice('n:3'), tool('call_1'), agent('m:4')]);
    expect(outline(fromAnotherApp)).toEqual(['u:1', 'm:2', 'n:3', 'steps:call_1 ×1', 'm:4']);

    const midRun = rows([user('u:1'), thought('t:2'), agent('m:3'), notice('n:4'), tool('tool-5'), agent('m:6')]);
    expect(outline(midRun)).toEqual(['u:1', 'steps:t:2 ×1', 'm:3', 'n:4', 'steps:tool-5 ×1', 'm:6']);
  });

  it('names the newest step of the run that ends a running turn', () => {
    const items = [user('u:1'), thought('t:2'), agent('m:3'), tool('tool-4'), thought('t:5'), tool('tool-6', 'pytest -q')];

    const [running] = stepsRows(rows(items, { running: true }));

    expect(running).toMatchObject({ id: stepsRowId('t:2'), count: 5, live: true, current: 'pytest -q' });
    expect(stepsRows(rows(items.slice(0, 5), { running: true }))[0]?.current).toBe('Thinking');
    expect(stepsRows(rows([user('u:1'), tool('tool-2', '')], { running: true }))[0]?.current).toBe('Tool call');
  });

  it('marks no run live when the turn is not running or the run is not the newest thing', () => {
    const working = [user('u:1'), thought('t:2'), tool('tool-3')];
    const idle = stepsRows(rows(working));
    expect(idle).toEqual([expect.objectContaining({ live: false, current: null })]);

    const answered = stepsRows(rows([...working, agent('m:4')], { running: true }));
    expect(answered).toEqual([expect.objectContaining({ live: false, current: null })]);

    const twoRuns = stepsRows(rows([user('u:1'), thought('t:2'), notice('n:3'), tool('tool-4')], { running: true }));
    expect(twoRuns.map((row) => row.live)).toEqual([false, true]);
  });

  it('lists the steps of an open row right after it, nested', () => {
    const items = [user('u:1'), thought('t:2'), agent('m:3'), tool('tool-4'), agent('m:5'), user('u:6'), tool('tool-7'), agent('m:8')];

    const list = rows(items, { open: new Set([stepsRowId('t:2')]) });

    expect(outline(list)).toEqual([
      'u:1',
      'steps:t:2 ×3',
      '  t:2',
      '  m:3',
      '  tool-4',
      'm:5',
      'u:6',
      'steps:tool-7 ×1',
      'm:8',
    ]);
    expect(stepsRows(list).map((row) => row.open)).toEqual([true, false]);
  });
});

describe('subagentTree', () => {
  const ids = (list: readonly ChatItem[] | undefined) => list?.map((item) => item.id);

  it('moves the tool calls of a card and the cards nested in it out of the main flow', () => {
    const items = [
      user('u:1'),
      card('c1'),
      ownedTool('c1:read', 'c1'),
      card('c2', 'c1'),
      ownedTool('c2:grep', 'c2'),
      ownedTool('c9:run', 'c9'),
      agent('m:2'),
    ];

    const tree = subagentTree(items);

    expect(ids(tree.visible)).toEqual(['u:1', 'sa:c1', 'c9:run', 'm:2']);
    expect(ids(tree.tools.get('c1'))).toEqual(['c1:read']);
    expect(ids(tree.tools.get('c2'))).toEqual(['c2:grep']);
    expect(ids(tree.children.get('c1'))).toEqual(['sa:c2']);
    expect(tree.tools.has('c9')).toBe(false);
  });

  it('keeps a card in the main flow when its parent card is absent or the parents loop', () => {
    const orphan = subagentTree([card('c2', 'gone'), ownedTool('c2:grep', 'c2')]);
    expect(ids(orphan.visible)).toEqual(['sa:c2']);
    expect(ids(orphan.tools.get('c2'))).toEqual(['c2:grep']);

    const loop = subagentTree([card('c1', 'c2'), card('c2', 'c1'), card('c3', 'c1')]);
    expect(ids(loop.visible)).toEqual(['sa:c1', 'sa:c2', 'sa:c3']);
    expect(loop.children.size).toBe(0);
  });

  it('returns the same list when no card takes anything', () => {
    const items = [user('u:1'), ownedTool('c1:read', 'c1'), agent('m:2')];

    expect(subagentTree(items).visible).toBe(items);
  });

  it('never folds a card and folds the agent message before it as work', () => {
    const tree = subagentTree([user('u:1'), agent('m:2'), card('c1'), ownedTool('c1:read', 'c1'), agent('m:3')]);

    expect(outline(rows([...tree.visible]))).toEqual(['u:1', 'steps:m:2 ×1', 'sa:c1', 'm:3']);
    expect(outline(rows([...tree.visible], { focus: false }))).toEqual(['u:1', 'm:2', 'sa:c1', 'm:3']);
  });
});
