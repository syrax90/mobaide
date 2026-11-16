/**
 * How the Herdr dashboard reads a snapshot. Pure, so the ordering and the
 * wording — which Android and iOS share — are testable without a DOM.
 */
import type {
  HerdrPane,
  HerdrServer,
  HerdrSnapshot,
  HerdrTab,
  HerdrWorkspace,
} from '../protocol/dto/herdr';

/** What a pane shows. `none` is a pane without an agent. */
export type HerdrStatus = 'blocked' | 'failed' | 'done' | 'working' | 'idle' | 'unknown' | 'none';

export interface HerdrChip {
  text: string;
  /** A `.session-attention-*` tone, so the chips match the session cards. */
  tone: 'permission' | 'failed' | 'ready' | 'working' | 'waiting';
}

export interface HerdrPaneRow {
  server: HerdrServer;
  workspace: HerdrWorkspace;
  tab: HerdrTab;
  pane: HerdrPane;
  status: HerdrStatus;
}

/** Which `turn` of each pane the user has seen on this device, by `paneSeenKey`. */
export type SeenTurns = Readonly<Record<string, number>>;

const AGENT_LABELS: Readonly<Record<string, string>> = {
  claude: 'Claude Code',
  codex: 'Codex',
  antigravity: 'Antigravity',
  opencode: 'OpenCode',
  copilot: 'GitHub Copilot',
  cursor: 'Cursor',
  pi: 'Pi',
};

/** "Needs you" first, idle last: the order of the Agents list. */
const STATUS_ORDER: readonly HerdrStatus[] = [
  'blocked',
  'failed',
  'done',
  'working',
  'idle',
  'unknown',
  'none',
];

export function paneSeenKey(agentId: string, serverId: string, paneId: string): string {
  return `${agentId}\u0000${serverId}\u0000${paneId}`;
}

/** The display name of an agent; a name Mobaide does not know stays as it is. */
export function herdrAgentLabel(agent: string): string {
  return Object.hasOwn(AGENT_LABELS, agent) ? (AGENT_LABELS[agent] as string) : agent;
}

/**
 * A pane's status on this device. A `done` whose `turn` the user already saw
 * here is `idle`: Mobaide keeps "seen" per device, because marking a pane seen
 * in herdr would move the view of a user at the desktop.
 */
export function herdrStatus(pane: HerdrPane, seenTurn: number | undefined): HerdrStatus {
  if (pane.agent === undefined) return 'none';
  switch (pane.status) {
    case 'blocked':
    case 'failed':
    case 'working':
    case 'idle':
      return pane.status;
    case 'done':
      return pane.turn !== undefined && pane.turn === seenTurn ? 'idle' : 'done';
    default:
      return 'unknown';
  }
}

export function herdrChip(status: HerdrStatus): HerdrChip | null {
  switch (status) {
    case 'blocked':
      return { text: 'Needs you', tone: 'permission' };
    case 'failed':
      return { text: 'Failed', tone: 'failed' };
    case 'done':
      return { text: 'Done', tone: 'ready' };
    case 'working':
      return { text: 'Working', tone: 'working' };
    case 'idle':
      return { text: 'Idle', tone: 'waiting' };
    default:
      return null;
  }
}

/** Every pane of every ready server, in tree order. */
export function herdrPaneRows(
  agentId: string,
  snapshot: HerdrSnapshot | undefined,
  seen: SeenTurns,
): HerdrPaneRow[] {
  const rows: HerdrPaneRow[] = [];
  for (const server of snapshot?.servers ?? []) {
    for (const workspace of server.workspaces) {
      for (const tab of workspace.tabs) {
        for (const pane of tab.panes) {
          rows.push({
            server,
            workspace,
            tab,
            pane,
            status: herdrStatus(pane, seen[paneSeenKey(agentId, server.server_id, pane.pane_id)]),
          });
        }
      }
    }
  }
  return rows;
}

/** The panes that hold an agent, the ones that need the user first. */
export function herdrAgentRows(rows: readonly HerdrPaneRow[]): HerdrPaneRow[] {
  return rows
    .filter((row) => row.status !== 'none')
    .map((row, index) => ({ row, index }))
    .sort(
      (a, b) =>
        STATUS_ORDER.indexOf(a.row.status) - STATUS_ORDER.indexOf(b.row.status) ||
        a.index - b.index,
    )
    .map(({ row }) => row);
}

/** The most urgent status among some panes, for a workspace or a tab. */
export function herdrRollup(statuses: readonly HerdrStatus[]): HerdrStatus {
  for (const status of STATUS_ORDER) {
    if (statuses.includes(status)) return status;
  }
  return 'none';
}

function plural(count: number, noun: string): string {
  return `${String(count)} ${noun}${count === 1 ? '' : 's'}`;
}

/** The one-line summary on the session list: "3 agents · 1 needs you". */
export function herdrSummary(rows: readonly HerdrPaneRow[]): string {
  const agents = rows.filter((row) => row.status !== 'none');
  if (agents.length === 0) {
    return rows.length === 0 ? 'No terminals' : plural(rows.length, 'terminal');
  }
  const parts = [plural(agents.length, 'agent')];
  const count = (status: HerdrStatus) => agents.filter((row) => row.status === status).length;
  const blocked = count('blocked');
  const failed = count('failed');
  const done = count('done');
  const working = count('working');
  if (blocked > 0) parts.push(`${String(blocked)} need${blocked === 1 ? 's' : ''} you`);
  if (failed > 0) parts.push(`${String(failed)} failed`);
  if (done > 0) parts.push(`${String(done)} done`);
  if (working > 0) parts.push(`${String(working)} working`);
  return parts.join(' · ');
}

/** "Herdr" for the usual server; the session name and the product otherwise. */
export function herdrServerLabel(server: HerdrServer, servers: readonly HerdrServer[]): string {
  const product = server.product === 'core' ? 'Herdr app' : 'Herdr';
  const sameProduct = servers.filter((other) => other.product === server.product).length;
  if (server.name === 'default' && sameProduct <= 1) return product;
  return `${product} · ${server.name}`;
}

/** What the pane header and the lists call a pane. */
export function herdrPaneTitle(pane: HerdrPane): string {
  if (pane.title.trim() !== '') return pane.title;
  return pane.agent !== undefined ? herdrAgentLabel(pane.agent) : pane.pane_id;
}

export function findHerdrPane(
  snapshot: HerdrSnapshot | undefined,
  serverId: string,
  paneId: string,
): { server: HerdrServer; pane: HerdrPane } | null {
  const server = snapshot?.servers.find((candidate) => candidate.server_id === serverId);
  if (server === undefined) return null;
  for (const workspace of server.workspaces) {
    for (const tab of workspace.tabs) {
      const pane = tab.panes.find((candidate) => candidate.pane_id === paneId);
      if (pane !== undefined) return { server, pane };
    }
  }
  return null;
}
