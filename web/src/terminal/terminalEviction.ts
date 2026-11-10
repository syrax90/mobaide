/**
 * The seam through which the sessions list tells the terminal store that a
 * session is gone.
 *
 * iOS wires this directly (`SessionRepository` calls into its terminal store),
 * but on web `terminalStore` imports `sessionModel`, which imports
 * `@xterm/xterm` and its CSS — roughly 740 KB that `app/router.tsx` deliberately
 * confines to the one lazy route. A direct import from the realtime layer, which
 * `main.tsx` pulls in eagerly, would drag the whole emulator into the initial
 * bundle.
 *
 * So: this module has **no imports at all**, exactly like `realtime/liveChannels`.
 * `terminalStore` registers its evictor when the terminal chunk loads; before
 * that there are no models, so every call is a no-op.
 */
export type TerminalEvictor = (agentId: string, sessionIds: readonly string[]) => void;

let evictor: TerminalEvictor | null = null;
/** The chat store's evictor, registered the same way when the chat chunk loads. */
let chatEvictor: TerminalEvictor | null = null;

export function setTerminalEvictor(next: TerminalEvictor | null): void {
  evictor = next;
}

export function setChatEvictor(next: TerminalEvictor | null): void {
  chatEvictor = next;
}

/** Dispose any live terminal or chat model for these sessions. No-op before their chunks load. */
export function evictTerminals(agentId: string, sessionIds: readonly string[]): void {
  if (sessionIds.length === 0) return;
  evictor?.(agentId, sessionIds);
  chatEvictor?.(agentId, sessionIds);
}
