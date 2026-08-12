/**
 * When to stop consuming a session's PTY output.
 *
 * The session socket has no pause frame — C→S is only `i`/`x`/`m`/`r`, and the
 * relay routes `CH_SESSION_IO` solely to clients registered on
 * `session_io:<sid>` — so closing the socket is the only way to stop the bytes.
 * That makes suspension expensive to undo: the agent replays its **entire**
 * ring on every socket open (`state → 'b' → N×'o' → 'e'`, 4 MiB by default,
 * uncompressed), and the client wipes its emulator before writing it.
 *
 * So neither half of the obvious policy works alone:
 *
 * * **Time alone** ("hidden for a minute ⇒ suspend") charges an idle session a
 *   full replay to save the ~0 bytes it was not sending.
 * * **Bytes alone** ("spent a lot ⇒ suspend") fires on short hides, where
 *   staying attached was already cheaper than the replay it triggers.
 *
 * Suspension only pays when the session is *both* unwatched for a while *and*
 * actually spending, which is why this is a conjunction. `FIREHOSE_BYTES` is
 * the one escape hatch: a session dumping tens of MiB should not get to finish
 * the grace window first.
 *
 * Deliberately pure and timer-free — the caller evaluates it as frames arrive,
 * so a session that goes quiet after crossing a threshold is simply never
 * re-examined, and costs nothing. The same three constants are mirrored on
 * Android and iOS.
 */

/**
 * Bytes an unwatched session must have delivered before suspending it can pay
 * for itself. One ring: below this, the replay costs more than staying.
 */
export const RECOUP_BYTES = 4 * 1024 * 1024;

/** Spend that ends the grace window early, for `yes`-grade output. */
export const FIREHOSE_BYTES = 32 * 1024 * 1024;

/**
 * How long a session must have been unwatched. Matches `HIDDEN_RESUME_MS` in
 * `realtime/resumeOnFocus`, and sits well inside Chrome's ~5 minute freeze
 * window for a hidden tab.
 */
export const GRACE_MS = 60_000;

export interface BackgroundSpend {
  /** Milliseconds this session has been continuously unwatched. */
  unwatchedMs: number;
  /** Live output bytes since it stopped being watched. Replay spans excluded. */
  bytes: number;
}

export function shouldSuspend({ unwatchedMs, bytes }: BackgroundSpend): boolean {
  if (bytes < RECOUP_BYTES) return false;
  if (bytes >= FIREHOSE_BYTES) return true;
  return unwatchedMs >= GRACE_MS;
}
