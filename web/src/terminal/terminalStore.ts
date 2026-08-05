/**
 * The live terminal session models, keyed by `session:<agent>:<session>`.
 *
 * The models outlive their views (that is the whole point — a remount must not
 * cost a fresh 4 MiB replay), so two separate things bound the set:
 *
 * * **Death.** `evictTerminalSessions` drops a model once the agent says its
 *   session is gone — from a `sessions.changed` snapshot or a stop mutation.
 *   This is the iOS behaviour (`SessionRepository` evicting on the session
 *   dying); the sessions list is what finally delivers the signal on web.
 * * **Memory.** `MAX_RETAINED` still caps how many *live* sessions stay
 *   attached at once, since a user can hold several running sessions open and
 *   each one owns an emulator plus its scrollback.
 *
 * `stopAllRealtime()` reaches these through the disposable registry each model
 * self-registers with, which is why `forget` exists: a model disposed from
 * under the store has to leave, or the next `acquire` would hand back a dead
 * emulator.
 */
import { create } from 'zustand';
import { TerminalSessionModel } from './sessionModel';
import { sessionChannelName } from '../realtime/channels';
import { setTerminalEvictor } from './terminalEviction';

/** How many attached sessions stay resident. */
const MAX_RETAINED = 3;

interface TerminalStoreState {
  models: Record<string, TerminalSessionModel>;
  /** Keys most-recently-acquired last. */
  order: string[];
}

export const useTerminalStore = create<TerminalStoreState>()(() => ({
  models: {},
  order: [],
}));

function touch(key: string, order: readonly string[]): string[] {
  return [...order.filter((entry) => entry !== key), key];
}

/**
 * The terminal currently on screen, exempt from death-driven eviction.
 *
 * A session ending under the user must leave its output readable — the screen
 * says so, and both mobile clients behave that way. Disposing the model the user
 * is looking at would destroy the scrollback and hand the re-acquire effect an
 * empty emulator, so the one visible terminal opts out. Nothing is leaked: the
 * exemption ends when the screen unmounts, and `MAX_RETAINED` still bounds the
 * set. Background terminals for dead sessions are evicted as before.
 */
let mountedKey: string | null = null;

export function setMountedTerminal(key: string): void {
  mountedKey = key;
}

/**
 * Clears only if `key` is still the mounted one — a route switch mounts the
 * next terminal before the previous one's cleanup runs.
 */
export function clearMountedTerminal(key: string): void {
  if (mountedKey === key) mountedKey = null;
}

/**
 * Return the model for a session, building and connecting one if needed.
 *
 * Called from a React effect, so it must be idempotent for the same key —
 * StrictMode mounts every effect twice in development.
 */
export function acquireTerminal(
  agentId: string,
  sessionId: string,
): TerminalSessionModel {
  const key = sessionChannelName(agentId, sessionId);
  const { models, order } = useTerminalStore.getState();
  const existing = models[key];
  if (existing !== undefined) {
    useTerminalStore.setState({ order: touch(key, order) });
    return existing;
  }

  const model = new TerminalSessionModel(agentId, sessionId, {
    onDispose: () => {
      forgetTerminal(key);
    },
  });
  const nextOrder = touch(key, order);
  const evicted = nextOrder.slice(0, Math.max(0, nextOrder.length - MAX_RETAINED));
  const nextModels: Record<string, TerminalSessionModel> = {
    ...models,
    [key]: model,
  };
  for (const staleKey of evicted) delete nextModels[staleKey];
  useTerminalStore.setState({
    models: nextModels,
    order: nextOrder.slice(evicted.length),
  });
  // After the store write, so a model disposing itself synchronously cannot
  // race the entry it is trying to remove.
  for (const staleKey of evicted) models[staleKey]?.dispose();
  return model;
}

/**
 * Dispose the models for sessions the agent reports as gone.
 *
 * `dispose()` fires `onDispose`, which calls `forgetTerminal`, so the store
 * write is already handled — this only has to find them.
 */
export function evictTerminalSessions(
  agentId: string,
  sessionIds: readonly string[],
): void {
  const { models } = useTerminalStore.getState();
  for (const sessionId of sessionIds) {
    const key = sessionChannelName(agentId, sessionId);
    if (key === mountedKey) continue;
    models[key]?.dispose();
  }
}

setTerminalEvictor(evictTerminalSessions);

/** Drop a model's entry. Called by the model itself as it disposes. */
export function forgetTerminal(key: string): void {
  const { models, order } = useTerminalStore.getState();
  const { [key]: removed, ...rest } = models;
  if (removed === undefined) return;
  useTerminalStore.setState({
    models: rest,
    order: order.filter((entry) => entry !== key),
  });
}
