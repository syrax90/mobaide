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

/**
 * The five values the agent's `_STATE_MAP` can emit. Kept as a plain string on
 * the schema — a newer agent inventing a sixth must not make the frame
 * undecodable, and the only behavioural read is the `running` comparison the
 * send gates make.
 */
export const TERMINAL_STATUS_RUNNING = 'running';

export const terminalStateSchema = z.looseObject({
  status: z.string(),
  pid: optionalWireNumber(),
  exit_code: optionalWireNumber(),
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
