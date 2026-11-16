/**
 * The `s` (state) frame body carried on the binary PTY socket.
 *
 * The agent emits one on every socket open, ahead of the replay markers, and
 * again whenever the process transitions (`mobaide_agent/mobaide/agent/ws/
 * session_io.py`). `status` is the agent's `_STATE_MAP` value; `pid` rides along
 * when the process exists and `exit_code` when it has ended.
 */
import { z } from 'zod';
import { optionalWireNumber } from '../wireSchema';
import { parseWireJson } from '../wireJson';
import { PANE_CLIENT_ID } from '../paneClientId';

/**
 * The five values the agent's `_STATE_MAP` can emit. Kept as a plain string on
 * the schema — a newer agent inventing a sixth must not make the frame
 * undecodable, and the only behavioural read is the `running` comparison the
 * send gates make.
 */
export const TERMINAL_STATUS_RUNNING = 'running';

/**
 * The `pane` block of a Herdr pane session (PROTOCOL.md, "Herdr dashboard").
 * The stream is drawn for `cols` x `rows`, the real size of the pane. In
 * `control` mode one client, named by `controller`, owns that size; every other
 * client still draws the given grid.
 */
export const terminalPaneSchema = z.looseObject({
  mode: z.string(),
  controller: z.string().nullable().optional(),
  cols: optionalWireNumber(),
  rows: optionalWireNumber(),
});

export const terminalStateSchema = z.looseObject({
  status: z.string(),
  pid: optionalWireNumber(),
  exit_code: optionalWireNumber(),
  // Optional as a key, unlike the transformed fields above: only a Herdr pane
  // session sends it, and a state built by hand need not name it.
  // A block this build cannot read must not cost the frame its `status`.
  pane: terminalPaneSchema.nullable().optional().catch(null),
});

export type TerminalState = z.infer<typeof terminalStateSchema>;

/** Decode an `s` frame body. Returns null for anything that does not parse. */
export function decodeTerminalState(json: string): TerminalState | null {
  let parsed: unknown;
  try {
    parsed = parseWireJson(json);
  } catch {
    return null;
  }
  const result = terminalStateSchema.safeParse(parsed);
  return result.success ? result.data : null;
}

/** Whether the session is accepting input. Mobile gates every send on this. */
export function isSessionRunning(state: TerminalState | null): boolean {
  return state?.status === TERMINAL_STATUS_RUNNING;
}

export const PANE_MODE_WATCH = 'watch';
export const PANE_MODE_CONTROL = 'control';

export interface TerminalGrid {
  cols: number;
  rows: number;
}

/** Whether this client is the one that controls the pane. */
export function isPaneController(
  state: TerminalState | null,
  clientId: string = PANE_CLIENT_ID,
): boolean {
  const pane = state?.pane;
  return pane?.mode === PANE_MODE_CONTROL && pane.controller === clientId;
}

/** Whether another client controls the pane. */
export function isPaneControlledElsewhere(
  state: TerminalState | null,
  clientId: string = PANE_CLIENT_ID,
): boolean {
  return state?.pane?.mode === PANE_MODE_CONTROL && !isPaneController(state, clientId);
}

/**
 * The grid the agent dictates, or null when this client owns the size: a
 * session that is not a Herdr pane, a pane this client controls, or a block
 * that does not carry a usable grid yet.
 */
export function remoteGrid(
  state: TerminalState | null,
  clientId: string = PANE_CLIENT_ID,
): TerminalGrid | null {
  const pane = state?.pane;
  if (pane === undefined || pane === null) return null;
  if (pane.mode !== PANE_MODE_WATCH && pane.mode !== PANE_MODE_CONTROL) return null;
  if (isPaneController(state, clientId)) return null;
  const { cols, rows } = pane;
  if (cols === undefined || rows === undefined) return null;
  if (!Number.isInteger(cols) || !Number.isInteger(rows) || cols <= 0 || rows <= 0) return null;
  return { cols, rows };
}
