// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { herdrSnapshotSchema, type HerdrPane } from '../protocol/dto/herdr';
import {
  herdrAgentRows,
  herdrPaneRows,
  herdrRollup,
  herdrStatus,
  paneSeenKey,
} from './herdrDisplay';

const pane = (over: Record<string, unknown> = {}): HerdrPane =>
  herdrSnapshotSchema.parse({
    servers: [
      {
        server_id: 's',
        workspaces: [
          {
            workspace_id: 'w',
            tabs: [{ tab_id: 't', panes: [{ pane_id: 'p', agent: 'claude', ...over }] }],
          },
        ],
      },
    ],
  }).servers[0]!.workspaces[0]!.tabs[0]!.panes[0]!;

/** One server `tui:default` whose panes are named by their status. */
const snapshot = (panes: Record<string, unknown>[], serverId = 'tui:default') =>
  herdrSnapshotSchema.parse({
    servers: [
      {
        server_id: serverId,
        workspaces: [{ workspace_id: 'w1', tabs: [{ tab_id: 'w1:t1', panes }] }],
      },
    ],
  });

describe('herdrStatus', () => {
  it('shows a finished turn as done until this device has seen that turn', () => {
    const done = pane({ status: 'done', turn: 12 });

    expect(herdrStatus(done, undefined)).toBe('done');
    expect(herdrStatus(done, 11)).toBe('done');
    expect(herdrStatus(done, 12)).toBe('idle');
  });

  it('never treats a done without a turn as seen', () => {
    // No turn to compare: hiding it would lose a "your move" for good.
    expect(herdrStatus(pane({ status: 'done' }), undefined)).toBe('done');
  });

  it('keeps a seen turn from hiding an agent that needs the user again', () => {
    expect(herdrStatus(pane({ status: 'blocked', turn: 12 }), 12)).toBe('blocked');
    expect(herdrStatus(pane({ status: 'failed', turn: 12 }), 12)).toBe('failed');
  });

  it('shows a pane without an agent as a plain terminal, whatever its status', () => {
    expect(herdrStatus(pane({ agent: null, status: 'blocked' }), undefined)).toBe('none');
  });

  it('keeps an agent whose status this client does not know', () => {
    expect(herdrStatus(pane({ status: 'hibernating' }), undefined)).toBe('unknown');
    expect(herdrStatus(pane({ status: null }), undefined)).toBe('unknown');
  });
});

describe('herdrPaneRows', () => {
  const state = snapshot([{ pane_id: 'w1:p1', agent: 'claude', status: 'done', turn: 3 }]);

  it('reads the seen turn of this agent, server and pane only', () => {
    const status = (seen: Record<string, number>) =>
      herdrPaneRows('agent-1', state, seen)[0]?.status;

    expect(status({ [paneSeenKey('agent-1', 'tui:default', 'w1:p1')]: 3 })).toBe('idle');
    // The same pane id on another computer, or on another herdr server of
    // this one, is another pane.
    expect(status({ [paneSeenKey('agent-2', 'tui:default', 'w1:p1')]: 3 })).toBe('done');
    expect(status({ [paneSeenKey('agent-1', 'tui:work', 'w1:p1')]: 3 })).toBe('done');
  });

  it('does not let ids that share a prefix collide', () => {
    expect(paneSeenKey('a', 'b:c', 'd')).not.toBe(paneSeenKey('a:b', 'c', 'd'));
    expect(paneSeenKey('a', 'b', 'c:d')).not.toBe(paneSeenKey('a', 'b:c', 'd'));
  });

  it('has no rows before the first snapshot', () => {
    expect(herdrPaneRows('agent-1', undefined, {})).toEqual([]);
  });
});

describe('herdrAgentRows', () => {
  it('puts the agents that need the user first and leaves plain terminals out', () => {
    const rows = herdrPaneRows(
      'agent-1',
      snapshot([
        { pane_id: 'idle', agent: 'claude', status: 'idle' },
        { pane_id: 'shell' },
        { pane_id: 'working', agent: 'codex', status: 'working' },
        { pane_id: 'done-1', agent: 'claude', status: 'done', turn: 1 },
        { pane_id: 'blocked', agent: 'codex', status: 'blocked' },
        { pane_id: 'failed', agent: 'pi', status: 'failed' },
        { pane_id: 'done-2', agent: 'claude', status: 'done', turn: 1 },
      ]),
      {},
    );

    expect(herdrAgentRows(rows).map((row) => row.pane.pane_id)).toEqual([
      'blocked',
      'failed',
      'done-1',
      'done-2',
      'working',
      'idle',
    ]);
  });
});

describe('herdrRollup', () => {
  it('reports the most urgent status of a group', () => {
    expect(herdrRollup(['idle', 'working', 'done'])).toBe('done');
    expect(herdrRollup(['done', 'none', 'blocked', 'failed'])).toBe('blocked');
    expect(herdrRollup(['none', 'none'])).toBe('none');
    expect(herdrRollup([])).toBe('none');
  });
});
