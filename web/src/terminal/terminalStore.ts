/**
 * The live terminal session models, keyed by `session:<agent>:<session>`.
 *
 * The models outlive their views (that is the whole point — a remount must not
 * cost a fresh 4 MiB replay), so something has to bound how many stay attached.
 * iOS keeps one per session until `SessionRepository` evicts it on the session
 * dying; the web client has no session list yet to deliver that signal, so it
 * caps the set and drops the least recently acquired instead.
 *
 * `stopAllRealtime()` reaches these through the disposable registry each model
 * self-registers with, which is why `forget` exists: a model disposed from
 * under the store has to leave, or the next `acquire` would hand back a dead
 * emulator.
 */
import { create } from 'zustand';
import { TerminalSessionModel } from './sessionModel';
import { sessionChannelName } from '../realtime/channels';

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
