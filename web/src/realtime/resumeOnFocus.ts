/**
 * Re-open the long-lived sockets after the two events that silently kill them.
 *
 * A browser exposes no `ping()`, so a half-open socket — NAT rebind, Wi-Fi to
 * cellular, a laptop waking from sleep — sits at `readyState === OPEN` with no
 * data and no close event until the OS TCP stack gives up, which can be
 * minutes or never. Both mobile clients avoid this with a 30 s application ping
 * (Android `NetworkModule`'s `pingInterval`, iOS `NWWebSocketTransport`); the
 * relay's own `_pinger` only reaches agents, not mobile clients.
 *
 * WEB-10 makes that gap load-bearing: attention notifications ride the
 * user-events socket, and the tab-lifecycle contract deliberately keeps it open
 * while hidden — so a laptop closed overnight would wake to a socket that looks
 * healthy and delivers nothing.
 *
 * Two triggers, both chosen so a healthy socket is never torn down for nothing:
 *
 * * `online` — a real network transition, and rare.
 * * Returning to a tab that was hidden **longer than the threshold**. Not every
 *   `visibilitychange`: alt-tabbing is constant, and reconnecting on each one
 *   would be its own handshake storm.
 *
 * This does not close the case of a path black-holing while the tab stays
 * visible and online. That needs a heartbeat the browser can observe, which is
 * a protocol change across all three clients — tracked separately.
 */
import { forceReconnectAllEvents } from './channelRegistry';

/**
 * Extra channels to re-open on a resume.
 *
 * The registry sweep covers the two account/agent-scoped events channels it owns.
 * A screen-scoped channel — the auto-scan monitor's — registers here instead,
 * because it is built by its consumer and `liveChannels` only models disposal.
 */
const subscribers = new Set<() => void>();

/** Register a channel to be re-opened on resume. Returns the unsubscriber. */
export function onResume(listener: () => void): () => void {
  subscribers.add(listener);
  return () => {
    subscribers.delete(listener);
  };
}

function resume(): void {
  forceReconnectAllEvents();
  for (const listener of [...subscribers]) listener();
}

/** How long a tab must have been hidden before returning counts as a resume. */
const HIDDEN_RESUME_MS = 60_000;

/**
 * The slice of `window` / `document` this needs. Narrower than `Window`'s
 * overloaded `addEventListener` so a test double can be a plain object rather
 * than a cast.
 */
export interface ResumeEventTarget {
  addEventListener: (type: string, listener: () => void) => void;
  removeEventListener: (type: string, listener: () => void) => void;
}

/**
 * Install the listeners. Returns the uninstaller.
 *
 * `now` is injected so a test can drive the threshold without a fake clock.
 */
export function observeResume(
  now: () => number = () => Date.now(),
  target: ResumeEventTarget = window,
  doc: ResumeEventTarget & { hidden: boolean } = document,
): () => void {
  let hiddenSince: number | null = doc.hidden ? now() : null;

  const onOnline = (): void => {
    resume();
  };

  const onVisibility = (): void => {
    if (doc.hidden) {
      hiddenSince = now();
      return;
    }
    const since = hiddenSince;
    hiddenSince = null;
    if (since !== null && now() - since >= HIDDEN_RESUME_MS) resume();
  };

  target.addEventListener('online', onOnline);
  doc.addEventListener('visibilitychange', onVisibility);
  return () => {
    target.removeEventListener('online', onOnline);
    doc.removeEventListener('visibilitychange', onVisibility);
  };
}
