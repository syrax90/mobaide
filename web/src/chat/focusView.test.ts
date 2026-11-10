import { describe, expect, it } from 'vitest';
import type { ChatItem } from './chatWire';
import { stepsLabel, stepsRowId, timelineRows, type TimelineRow, type TimelineRowOptions } from './focusView';

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

describe('stepsLabel', () => {
  it('counts one step and many steps', () => {
    expect(stepsLabel(1)).toBe('1 step');
    expect(stepsLabel(4)).toBe('4 steps');
  });
});
