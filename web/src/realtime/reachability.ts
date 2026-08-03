/**
 * Drives TanStack Query's `onlineManager` from real reachability rather than
 * `navigator.onLine`, which only reports whether an interface is up.
 *
 * Marking the app offline pauses **mutations** as well as queries, so the
 * policy is deliberately conservative:
 *
 * - offline only after two consecutive *pre-open* failures of the user-events
 *   channel, so a single blip does not freeze the UI;
 * - back online the instant the socket opens, on any REST response, and
 *   whenever the channel is intentionally stopped — which is the signed-out
 *   state, where the login mutations must never be paused;
 * - while offline, a periodic probe of the anonymous `/api/version` endpoint
 *   is the escape hatch. It is not optional: once offline, TanStack pauses the
 *   very requests whose responses would otherwise clear the flag, so a
 *   WebSocket upgrade blocked by a proxy while HTTP works fine would
 *   otherwise pin the app offline permanently.
 */
import { onlineManager } from '@tanstack/react-query';
import type { ChannelEvent } from './webSocketChannel';

const OFFLINE_AFTER_FAILURES = 2;
const OFFLINE_PROBE_INTERVAL_MS = 15_000;
const PROBE_TIMEOUT_MS = 5_000;

/**
 * How recently HTTP must have worked for a WebSocket failure to be disbelieved.
 *
 * Without this the module oscillates in the exact scenario it exists for: WS
 * blocked but HTTP fine means two failures put the app offline, the probe
 * proves the server reachable and puts it back online, and the next two
 * failures repeat it — every flip firing a full `refetchOnReconnect` sweep,
 * forever. Independent proof that HTTP works therefore suppresses the offline
 * transition rather than merely undoing it.
 */
const REST_TRUST_WINDOW_MS = 60_000;

/** Anonymous, cheap, and already the app's bootstrap probe. */
const PROBE_PATH = '/api/version';

let consecutiveFailures = 0;
let declaredOffline = false;
let lastRestReachableAt = 0;
let probeTimer: ReturnType<typeof setInterval> | null = null;
let probeInFlight = false;
let probeFetch: typeof fetch = (...args) => fetch(...args);

function stopProbing(): void {
  if (probeTimer === null) return;
  clearInterval(probeTimer);
  probeTimer = null;
  probeInFlight = false;
}

async function probeOnce(): Promise<void> {
  // A blackholed network leaves a fetch pending far longer than the interval,
  // so without both guards probes stack up for the whole outage and land as a
  // burst when connectivity returns.
  if (probeInFlight) return;
  probeInFlight = true;
  try {
    // Any status at all is proof the server answered; only a rejected fetch
    // means still unreachable.
    await probeFetch(PROBE_PATH, {
      method: 'GET',
      cache: 'no-store',
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    markRestReachable();
    goOnline();
  } catch {
    // Still down — the next tick tries again.
  } finally {
    probeInFlight = false;
  }
}

function startProbing(): void {
  if (probeTimer !== null) return;
  // Probe immediately, not just on the first interval tick: TanStack pauses
  // mutations while offline, so an arming delay is a window in which a user
  // action sits silently frozen.
  void probeOnce();
  probeTimer = setInterval(() => {
    void probeOnce();
  }, OFFLINE_PROBE_INTERVAL_MS);
}

function markRestReachable(): void {
  lastRestReachableAt = Date.now();
}

function restRecentlyReachable(): boolean {
  return lastRestReachableAt !== 0 && Date.now() - lastRestReachableAt < REST_TRUST_WINDOW_MS;
}

function goOnline(): void {
  consecutiveFailures = 0;
  if (!declaredOffline) return;
  declaredOffline = false;
  stopProbing();
  onlineManager.setOnline(true);
}

function recordFailure(): void {
  consecutiveFailures += 1;
  if (declaredOffline || consecutiveFailures < OFFLINE_AFTER_FAILURES) return;
  // The WebSocket is the primary signal, but it is not the only transport. If
  // HTTP answered moments ago the socket is what is broken, not the network.
  if (restRecentlyReachable()) return;
  declaredOffline = true;
  onlineManager.setOnline(false);
  startProbing();
}

/** The socket opened — the server is reachable. */
export function reportRealtimeConnected(): void {
  goOnline();
}

/** The channel was stopped on purpose (sign-out, unmount). */
export function reportRealtimeStopped(): void {
  goOnline();
}

/** A REST call came back from the server, whatever its status. */
export function reportRestReachable(): void {
  markRestReachable();
  goOnline();
}

/**
 * Feed a channel's events into the reachability signal.
 *
 * A `disconnected` only counts as a failure when the socket never opened during
 * that connection cycle: dropping an established connection is one event, while
 * repeatedly failing to establish one is the pattern that means "the server is
 * not there".
 */
export function observeReachability(
  subscribe: (listener: (event: ChannelEvent) => void) => () => void,
): () => void {
  let openedThisCycle = false;
  return subscribe((event) => {
    if (event.kind === 'connected') {
      openedThisCycle = true;
      reportRealtimeConnected();
      return;
    }
    if (event.kind === 'disconnected') {
      if (!openedThisCycle) recordFailure();
      openedThisCycle = false;
    }
  });
}

/** Test seam — restores the initial "online, no failures, not probing" state. */
export function resetReachability(fetchImpl: typeof fetch = (...args) => fetch(...args)): void {
  consecutiveFailures = 0;
  declaredOffline = false;
  lastRestReachableAt = 0;
  stopProbing();
  probeFetch = fetchImpl;
  onlineManager.setOnline(true);
}
