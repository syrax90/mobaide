/**
 * Drives TanStack Query's `onlineManager` from real reachability rather than
 * `navigator.onLine`, which only reports whether an interface is up.
 *
 * Marking the app offline pauses **mutations** as well as queries, so the
 * policy is deliberately conservative:
 *
 * - two consecutive *pre-open* failures of the user-events channel are what
 *   raise the question, so a single blip does not freeze the UI;
 * - a failing WebSocket alone never answers it. Either HTTP was proven
 *   reachable moments ago, or the anonymous `/api/version` endpoint is probed
 *   before the app is declared offline — so a WebSocket upgrade blocked by a
 *   proxy while HTTP works fine does not take the app offline at all, rather
 *   than taking it offline and undoing that on the next probe;
 * - back online the instant the socket opens, on any REST response, and
 *   whenever the channel is intentionally stopped — which is the signed-out
 *   state, where the login mutations must never be paused;
 * - while offline the probe repeats on an interval, because TanStack pauses the
 *   very requests whose responses would otherwise clear the flag.
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
let pendingProbe: Promise<boolean> | null = null;
let probeFetch: typeof fetch = (...args) => fetch(...args);

/**
 * Bumped by every piece of positive evidence, including when the app is already
 * online.
 *
 * `goOnline()` returns early in that case and would otherwise leave no trace an
 * in-flight confirmation probe could observe — so a probe that rejected *after*
 * the socket came back would still declare the app offline, with a healthy
 * connection open and nothing to correct it until the next interval tick.
 */
let reachabilityGeneration = 0;

/**
 * Take ownership of `onlineManager`'s event source.
 *
 * TanStack installs its own `window` online/offline listener that calls
 * `setOnline` directly, and leaving it in place desyncs this module from the
 * flag it is trying to own — in both directions. A browser `offline` sets the
 * flag false while `declaredOffline` stays false, so `goOnline()` takes its
 * early return and never restores it, which would break the guarantee below
 * that signing out can never leave login mutations paused. A browser `online`
 * overrides a confirmed outage that `recordFailure()` can no longer re-assert.
 *
 * The browser's `online` event is still worth having as cheap positive
 * evidence, so it is routed through `goOnline()` like every other positive
 * signal. `offline` is deliberately ignored: an interface going down says
 * nothing about whether the relay is reachable, and that is what the WebSocket
 * and the probe are for.
 */
function takeOverOnlineManager(): void {
  onlineManager.setEventListener(() => {
    const handleOnline = (): void => {
      goOnline();
    };
    window.addEventListener('online', handleOnline);
    return () => {
      window.removeEventListener('online', handleOnline);
    };
  });
}

takeOverOnlineManager();

function stopProbing(): void {
  if (probeTimer === null) return;
  clearInterval(probeTimer);
  probeTimer = null;
}

async function runProbe(): Promise<boolean> {
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
    return true;
  } catch {
    return false;
  }
}

/**
 * Probe, sharing one in-flight request between concurrent callers.
 *
 * Sharing rather than declining matters: a caller told "another probe is
 * running" cannot tell that from "the server did not answer", and the offline
 * decision reads exactly that boolean. A blackholed network — the case the gate
 * exists to arbitrate — leaves the first probe pending for the full timeout,
 * which is precisely when a second caller arrives.
 */
function probeOnce(): Promise<boolean> {
  const existing = pendingProbe;
  if (existing !== null) return existing;
  const probe = runProbe();
  pendingProbe = probe;
  void probe.finally(() => {
    if (pendingProbe === probe) pendingProbe = null;
  });
  return probe;
}

function startProbing(): void {
  if (probeTimer !== null) return;
  // No immediate probe here: going offline is already gated on one that just
  // failed, so firing another straight away would only duplicate it.
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
  reachabilityGeneration += 1;
  if (!declaredOffline) {
    // Reconcile rather than assume. Taking over the event listener removes the
    // only routine way the flag drifts from this module's view of it, but the
    // flag is process-global and positive evidence should be able to correct it
    // whatever set it — otherwise a `false` this module never recorded is one
    // nothing can undo, and mutations stay paused for good.
    if (!onlineManager.isOnline()) onlineManager.setOnline(true);
    return;
  }
  declaredOffline = false;
  stopProbing();
  onlineManager.setOnline(true);
}

function recordFailure(): void {
  consecutiveFailures += 1;
  if (declaredOffline || consecutiveFailures < OFFLINE_AFTER_FAILURES) return;

  // The WebSocket is the primary signal, but it is not the only transport. If
  // HTTP answered moments ago the socket is what is broken, not the network.
  //
  // Resetting the counter here is what keeps the "two consecutive failures"
  // contract honest: leaving it climbing meant the first failure after the
  // trust window expired flipped offline on its own.
  if (restRecentlyReachable()) {
    consecutiveFailures = 0;
    return;
  }

  // No fresh HTTP evidence either way, so ask before declaring. Probing first
  // rather than flipping offline and probing afterwards is what stops a
  // WS-blocked-but-HTTP-fine network from oscillating: there, the probe
  // succeeds and the app simply never goes offline.
  consecutiveFailures = 0;
  void confirmOfflineByProbe();
}

async function confirmOfflineByProbe(): Promise<void> {
  if (declaredOffline) return;
  const generation = reachabilityGeneration;
  const reachable = await probeOnce();
  // Anything positive that landed while this was in flight outranks a stale
  // negative — the socket opening, a REST response, or a concurrent probe
  // succeeding.
  if (reachable || declaredOffline || generation !== reachabilityGeneration) return;
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
  reachabilityGeneration += 1;
  pendingProbe = null;
  stopProbing();
  probeFetch = fetchImpl;
  takeOverOnlineManager();
  onlineManager.setOnline(true);
}
